"""Tests for stage-release-artefacts staging pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.config import ArtefactConfig, StagingConfig
from stage_common.pipeline import (
    _render_template,
    _safe_destination_path,
    stage_artefacts,
)


class TestStageArtefacts:
    """Tests for the stage_artefacts function."""

    @staticmethod
    def _make_linux_config(
        workspace: Path,
        artefacts: list[ArtefactConfig],
    ) -> StagingConfig:
        """Return a minimal Linux StagingConfig for use in tests."""
        return StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=artefacts,
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )

    def test_stages_artefact(self, tmp_path: Path) -> None:
        """Artefacts are copied to staging directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        output_file = tmp_path / "output"

        config = self._make_linux_config(
            workspace, [ArtefactConfig(source="myapp", output="binary_path")]
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

        config = self._make_linux_config(
            workspace, [ArtefactConfig(source="missing", required=True)]
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

        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(source="myapp"),
                ArtefactConfig(source="optional", required=False),
            ],
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

        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="primary",
                    alternatives=["fallback"],
                ),
            ],
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

        config = self._make_linux_config(workspace, [ArtefactConfig(source="myapp")])

        result = stage_artefacts(config, output_file)

        checksum_file = result.staging_dir / "myapp.sha256"
        assert checksum_file.exists()
        contents = checksum_file.read_text(encoding="utf-8")
        assert "myapp" in contents

    @staticmethod
    def _make_powershell_workspace(
        tmp_path: Path,
        target: str = "x86_64-pc-windows-msvc",
    ) -> tuple[Path, Path]:
        """Create a workspace containing MyTool PowerShell sidecar source files."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_dir = (
            workspace
            / "target"
            / "orthohelp"
            / target
            / "release"
            / "powershell"
            / "MyTool"
        )
        help_dir = source_dir / "en-US"
        help_dir.mkdir(parents=True)
        for f in [
            source_dir / "MyTool.psm1",
            source_dir / "MyTool.psd1",
            help_dir / "MyTool-help.xml",
            help_dir / "about_MyTool.help.txt",
        ]:
            f.write_text(f.name, encoding="utf-8")
        return workspace, tmp_path / "output"

    @staticmethod
    def _powershell_artefact_configs() -> list[ArtefactConfig]:
        """Return the four optional ArtefactConfig entries for the MyTool sidecar."""
        base = "target/orthohelp/{target}/release/powershell/MyTool"
        return [
            ArtefactConfig(
                source=f"{base}/MyTool.psm1",
                destination="MyTool/MyTool.psm1",
                required=False,
            ),
            ArtefactConfig(
                source=f"{base}/MyTool.psd1",
                destination="MyTool/MyTool.psd1",
                required=False,
            ),
            ArtefactConfig(
                source=f"{base}/en-US/MyTool-help.xml",
                destination="MyTool/en-US/MyTool-help.xml",
                required=False,
            ),
            ArtefactConfig(
                source=f"{base}/en-US/about_MyTool.help.txt",
                destination="MyTool/en-US/about_MyTool.help.txt",
                required=False,
            ),
        ]

    def test_stages_windows_powershell_help_dir(self, tmp_path: Path) -> None:
        """Windows PowerShell sidecars are staged under the module directory."""
        workspace, output_file = self._make_powershell_workspace(tmp_path)

        config = StagingConfig(
            workspace=workspace,
            bin_name="mytool",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=self._powershell_artefact_configs(),
            platform="windows",
            arch="x86_64",
            target="x86_64-pc-windows-msvc",
        )

        result = stage_artefacts(config, output_file, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 4
        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert (result.staging_dir / "MyTool" / "MyTool.psd1").exists()
        assert (result.staging_dir / "MyTool" / "en-US" / "MyTool-help.xml").exists()
        assert (
            result.staging_dir / "MyTool" / "en-US" / "about_MyTool.help.txt"
        ).exists()
        assert result.powershell_help_dir == result.staging_dir / "MyTool"
        output = output_file.read_text(encoding="utf-8")
        assert f"powershell_help_dir={result.staging_dir / 'MyTool'}" in output

    def test_linux_skips_absent_optional_powershell_help(self, tmp_path: Path) -> None:
        """Linux staging succeeds when optional PowerShell sidecars are absent."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        binary = workspace / "mytool"
        binary.write_text("binary content", encoding="utf-8")
        output_file = tmp_path / "output"

        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(source="mytool"),
                ArtefactConfig(
                    source=(
                        "target/orthohelp/{target}/release/powershell/"
                        "MyTool/MyTool.psm1"
                    ),
                    destination="MyTool/MyTool.psm1",
                    required=False,
                ),
            ],
        )

        result = stage_artefacts(config, output_file, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 1
        assert result.powershell_help_dir is None
        assert "powershell_help_dir=" in output_file.read_text(encoding="utf-8")

    def test_missing_required_powershell_help_fails(self, tmp_path: Path) -> None:
        """Missing required PowerShell sidecars fail with the attempted path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="mytool",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[
                ArtefactConfig(
                    source=(
                        "target/orthohelp/{target}/release/powershell/"
                        "MyTool/MyTool.psm1"
                    ),
                    destination="MyTool/MyTool.psm1",
                ),
            ],
            platform="windows",
            arch="x86_64",
            target="x86_64-pc-windows-msvc",
        )

        with pytest.raises(StageError, match=r"MyTool/MyTool\.psm1"):
            stage_artefacts(config, output_file, ps_module_name="MyTool")

    def test_empty_ps_module_name_leaves_powershell_help_dir_empty(
        self, tmp_path: Path
    ) -> None:
        """The output stays empty unless the module name is provided."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_dir = workspace / "powershell" / "MyTool"
        source_dir.mkdir(parents=True)
        (source_dir / "MyTool.psm1").write_text("module", encoding="utf-8")
        output_file = tmp_path / "output"

        config = StagingConfig(
            workspace=workspace,
            bin_name="mytool",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[
                ArtefactConfig(
                    source="powershell/MyTool/MyTool.psm1",
                    destination="MyTool/MyTool.psm1",
                ),
            ],
            platform="windows",
            arch="x86_64",
            target="x86_64-pc-windows-msvc",
        )

        result = stage_artefacts(config, output_file)

        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert result.powershell_help_dir is None
        assert "powershell_help_dir=\n" in output_file.read_text(encoding="utf-8")


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
