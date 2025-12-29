"""Tests for the ``stage-release-artefacts`` action."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.config import ArtefactConfig, StagingConfig, load_config
from stage_common.environment import require_env_path
from stage_common.output import (
    prepare_output_data,
    validate_no_reserved_key_collisions,
    write_github_output,
)
from stage_common.pipeline import (
    _render_template,
    _safe_destination_path,
    stage_artefacts,
)
from stage_common.resolution import match_candidate_path


class TestRequireEnvPath:
    """Tests for the require_env_path function."""

    def test_returns_path_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Returns Path when environment variable is set."""
        monkeypatch.setenv("TEST_VAR", str(tmp_path))
        result = require_env_path("TEST_VAR")
        assert result == tmp_path

    def test_raises_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises StageError when environment variable is not set."""
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(StageError, match="not set"):
            require_env_path("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises StageError when environment variable is empty."""
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(StageError, match="not set"):
            require_env_path("TEST_VAR")


class TestArtefactConfig:
    """Tests for the ArtefactConfig dataclass."""

    def test_default_values(self) -> None:
        """ArtefactConfig has correct defaults."""
        config = ArtefactConfig(source="test.txt")
        assert config.source == "test.txt"
        assert config.required is True
        assert config.output is None
        assert config.destination is None
        assert config.alternatives == []


class TestStagingConfig:
    """Tests for the StagingConfig dataclass."""

    def test_staging_dir(self, tmp_path: Path) -> None:
        """staging_dir returns correct path."""
        config = StagingConfig(
            workspace=tmp_path,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )
        expected = tmp_path / "dist" / "myapp_linux_x86_64"
        assert config.staging_dir() == expected

    def test_as_template_context(self, tmp_path: Path) -> None:
        """as_template_context returns complete context."""
        config = StagingConfig(
            workspace=tmp_path,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            bin_ext=".exe",
            target_key="linux-x86_64",
        )
        context = config.as_template_context()
        assert context["bin_name"] == "myapp"
        assert context["platform"] == "linux"
        assert context["arch"] == "x86_64"
        assert context["bin_ext"] == ".exe"
        assert context["staging_dir_name"] == "myapp_linux_x86_64"


class TestLoadConfig:
    """Tests for the load_config function."""

    def test_loads_valid_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config parses valid TOML configuration."""
        monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
            encoding="utf-8",
        )

        config = load_config(config_file, "linux-x86_64")

        assert config.bin_name == "myapp"
        assert config.platform == "linux"
        assert len(config.artefacts) == 1

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """load_config raises for missing configuration file."""
        missing = tmp_path / "missing.toml"
        with pytest.raises(FileNotFoundError):
            load_config(missing, "linux-x86_64")

    def test_raises_for_missing_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config raises for missing target section."""
        monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[common]
bin_name = "myapp"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
            encoding="utf-8",
        )

        with pytest.raises(StageError, match="Missing configuration key"):
            load_config(config_file, "windows-x86_64")


class TestMatchCandidatePath:
    """Tests for the match_candidate_path function."""

    def test_matches_direct_path(self, tmp_path: Path) -> None:
        """Matches a direct file path."""
        target = tmp_path / "myapp"
        target.write_text("binary", encoding="utf-8")

        result = match_candidate_path(tmp_path, "myapp")

        assert result == target

    def test_matches_glob_pattern(self, tmp_path: Path) -> None:
        """Matches a glob pattern."""
        subdir = tmp_path / "dist"
        subdir.mkdir()
        target = subdir / "myapp.bin"
        target.write_text("binary", encoding="utf-8")

        result = match_candidate_path(tmp_path, "dist/*.bin")

        assert result == target

    def test_returns_none_for_no_match(self, tmp_path: Path) -> None:
        """Returns None when no file matches."""
        result = match_candidate_path(tmp_path, "nonexistent")
        assert result is None


class TestPrepareOutputData:
    """Tests for the prepare_output_data function."""

    def test_includes_all_fields(self, tmp_path: Path) -> None:
        """Output data includes all required fields."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        staged_file = staging_dir / "myapp"
        staged_file.touch()

        result = prepare_output_data(
            staging_dir=staging_dir,
            staged_paths=[staged_file],
            outputs={"binary_path": staged_file},
            checksums={"myapp": "abc123"},
        )

        assert "artifact_dir" in result
        assert "dist_dir" in result
        assert "staged_files" in result
        assert "artefact_map" in result
        assert "checksum_map" in result
        assert "binary_path" in result


class TestValidateNoReservedKeyCollisions:
    """Tests for the validate_no_reserved_key_collisions function."""

    def test_allows_non_reserved_keys(self, tmp_path: Path) -> None:
        """Non-reserved keys are allowed."""
        outputs = {"binary_path": tmp_path / "myapp"}
        validate_no_reserved_key_collisions(outputs)  # Should not raise

    def test_raises_for_reserved_key(self, tmp_path: Path) -> None:
        """Reserved keys raise StageError."""
        outputs = {"artifact_dir": tmp_path / "myapp"}
        with pytest.raises(StageError, match="reserved keys"):
            validate_no_reserved_key_collisions(outputs)


class TestWriteGithubOutput:
    """Tests for the write_github_output function."""

    def test_writes_simple_values(self, tmp_path: Path) -> None:
        """Simple string values are written correctly."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"key": "value"})

        contents = output_file.read_text(encoding="utf-8")
        assert "key=value" in contents

    def test_escapes_special_characters(self, tmp_path: Path) -> None:
        """Special characters are escaped."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"key": "line1\nline2"})

        contents = output_file.read_text(encoding="utf-8")
        assert "key=line1%0Aline2" in contents

    def test_normalizes_windows_paths(self, tmp_path: Path) -> None:
        """Windows paths are normalized when flag is set."""
        output_file = tmp_path / "output"
        write_github_output(
            output_file,
            {"path": "C:\\Users\\test"},
            normalize_windows_paths=True,
        )

        contents = output_file.read_text(encoding="utf-8")
        assert "path=C:/Users/test" in contents

    def test_writes_list_values_with_heredoc(self, tmp_path: Path) -> None:
        """List values are written using heredoc syntax."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"staged_files": ["file1.txt", "file2.txt"]})

        contents = output_file.read_text(encoding="utf-8")
        assert "staged_files<<gh_STAGED_FILES" in contents
        assert "file1.txt\nfile2.txt" in contents
        assert "gh_STAGED_FILES\n" in contents

    def test_list_values_preserve_windows_paths(self, tmp_path: Path) -> None:
        """List values are not affected by normalize_windows_paths flag."""
        output_file = tmp_path / "output"
        write_github_output(
            output_file,
            {"files": ["C:\\Users\\file1.txt", "C:\\Users\\file2.txt"]},
            normalize_windows_paths=True,
        )

        contents = output_file.read_text(encoding="utf-8")
        # List values use heredoc syntax, not the scalar formatting
        assert "C:\\Users\\file1.txt" in contents
        assert "C:\\Users\\file2.txt" in contents

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created if needed."""
        output_file = tmp_path / "subdir" / "nested" / "output"
        write_github_output(output_file, {"key": "value"})

        assert output_file.exists()
        contents = output_file.read_text(encoding="utf-8")
        assert "key=value" in contents


class TestStageArtefacts:
    """Tests for the stage_artefacts function."""

    def test_stages_artefact(self, tmp_path: Path) -> None:
        """Artefacts are copied to staging directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[ArtefactConfig(source="myapp", output="binary_path")],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

        result = stage_artefacts(config, output_file)

        assert len(result.staged_artefacts) == 1
        assert result.staging_dir.exists()
        assert (result.staging_dir / "myapp").exists()
        assert "myapp" in result.checksums

    def test_raises_for_missing_required_artefact(self, tmp_path: Path) -> None:
        """Missing required artefacts raise StageError."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[ArtefactConfig(source="missing", required=True)],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

        with pytest.raises(StageError, match="not found"):
            stage_artefacts(config, output_file)

    def test_skips_optional_artefact(self, tmp_path: Path) -> None:
        """Missing optional artefacts are skipped."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[
                ArtefactConfig(source="myapp"),
                ArtefactConfig(source="optional", required=False),
            ],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

        result = stage_artefacts(config, output_file)

        assert len(result.staged_artefacts) == 1

    def test_uses_alternative_when_primary_missing(self, tmp_path: Path) -> None:
        """Alternative source is used when primary source is missing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Primary source does not exist, but alternative does
        alt_source = workspace / "fallback"
        alt_source.write_text("fallback content", encoding="utf-8")
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[
                ArtefactConfig(
                    source="primary",
                    alternatives=["fallback"],
                ),
            ],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

        result = stage_artefacts(config, output_file)

        assert len(result.staged_artefacts) == 1
        staged_file = result.staged_artefacts[0]
        assert staged_file.name == "fallback"
        assert staged_file.read_text(encoding="utf-8") == "fallback content"

    def test_generates_checksum_sidecar(self, tmp_path: Path) -> None:
        """Checksum sidecar files are generated."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[ArtefactConfig(source="myapp")],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

        result = stage_artefacts(config, output_file)

        checksum_file = result.staging_dir / "myapp.sha256"
        assert checksum_file.exists()
        contents = checksum_file.read_text(encoding="utf-8")
        assert "myapp" in contents


class TestRenderTemplate:
    """Tests for the _render_template helper function."""

    def test_renders_valid_template(self) -> None:
        """Valid template keys are substituted."""
        result = _render_template("{name}-{version}", {"name": "app", "version": "1.0"})

        assert result == "app-1.0"

    def test_unknown_key_raises_stage_error(self) -> None:
        """Unknown template keys raise StageError."""
        with pytest.raises(StageError, match="Invalid template key"):
            _render_template("{unknown_key}", {"known_key": "value"})


class TestSafeDestinationPath:
    """Tests for the _safe_destination_path helper function."""

    def test_allows_nested_path(self, tmp_path: Path) -> None:
        """Nested paths within staging directory are allowed."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()

        result = _safe_destination_path(staging_dir, "subdir/file.txt")

        assert result.is_relative_to(staging_dir)

    def test_rejects_escape_attempt(self, tmp_path: Path) -> None:
        """Paths escaping the staging directory are rejected."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()

        with pytest.raises(StageError, match="escapes staging directory"):
            _safe_destination_path(staging_dir, "../evil/outside.txt")
