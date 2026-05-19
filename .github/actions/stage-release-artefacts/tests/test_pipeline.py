"""Tests for stage-release-artefacts staging pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.config import ArtefactConfig, StagingConfig
from stage_common.pipeline import (
    _is_disallowed_ps_module_name,
    _render_template,
    _resolve_powershell_help_dir,
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

        config = self._make_linux_config(
            workspace, [ArtefactConfig(source="myapp", output="binary_path")]
        )

        result = stage_artefacts(config)

        assert len(result.staged_artefacts) == 1
        assert result.staging_dir.exists()
        assert (result.staging_dir / "myapp").exists()
        assert "myapp" in result.checksums

    def test_raises_for_missing_required_artefact(self, tmp_path: Path) -> None:
        """Missing required artefacts raise StageError."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = self._make_linux_config(
            workspace, [ArtefactConfig(source="missing", required=True)]
        )

        with pytest.raises(StageError, match="not found"):
            stage_artefacts(config)

    def test_skips_optional_artefact(self, tmp_path: Path) -> None:
        """Missing optional artefacts are skipped."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(source="myapp"),
                ArtefactConfig(source="optional", required=False),
            ],
        )

        result = stage_artefacts(config)

        assert len(result.staged_artefacts) == 1

    def test_uses_alternative_when_primary_missing(self, tmp_path: Path) -> None:
        """Alternative source is used when primary source is missing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Primary source does not exist, but alternative does
        alt_source = workspace / "fallback"
        alt_source.write_text("fallback content", encoding="utf-8")

        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="primary",
                    alternatives=["fallback"],
                ),
            ],
        )

        result = stage_artefacts(config)

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

        config = self._make_linux_config(workspace, [ArtefactConfig(source="myapp")])

        result = stage_artefacts(config)

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
        workspace, _ = self._make_powershell_workspace(tmp_path)

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

        result = stage_artefacts(config, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 4
        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert (result.staging_dir / "MyTool" / "MyTool.psd1").exists()
        assert (result.staging_dir / "MyTool" / "en-US" / "MyTool-help.xml").exists()
        assert (
            result.staging_dir / "MyTool" / "en-US" / "about_MyTool.help.txt"
        ).exists()
        assert result.powershell_help_dir == result.staging_dir / "MyTool"

    def test_linux_skips_absent_optional_powershell_help(self, tmp_path: Path) -> None:
        """Linux staging succeeds when optional PowerShell sidecars are absent."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        binary = workspace / "mytool"
        binary.write_text("binary content", encoding="utf-8")

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

        result = stage_artefacts(config, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 1
        assert result.powershell_help_dir is None

    def test_missing_required_powershell_help_fails(self, tmp_path: Path) -> None:
        """Missing required PowerShell sidecars fail with the attempted path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

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
            stage_artefacts(config, ps_module_name="MyTool")

    def test_empty_ps_module_name_leaves_powershell_help_dir_empty(
        self, tmp_path: Path
    ) -> None:
        """The result stays empty unless the module name is provided."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_dir = workspace / "powershell" / "MyTool"
        source_dir.mkdir(parents=True)
        (source_dir / "MyTool.psm1").write_text("module", encoding="utf-8")

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

        result = stage_artefacts(config)

        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert result.powershell_help_dir is None


# Restricted to the Basic Multilingual Plane (max_codepoint=0xFFFF) to avoid
# macOS EILSEQ on supplementary characters. Excludes surrogates (Cs), C0/C1
# control characters (Cc), unassigned codepoints (Cn), Windows-illegal filename
# characters, and POSIX path separators.
PS_MODULE_NAMES = st.text(
    alphabet=st.characters(
        blacklist_characters='/\\\x00:*?"<>|',
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=12,
).filter(lambda value: value not in {".", ".."})
HYPOTHESIS_SETTINGS = settings(
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


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


class TestResolvePowerShellHelpDir:
    """Tests for the _resolve_powershell_help_dir helper function."""

    def test_returns_direct_child_module_dir(self, tmp_path: Path) -> None:
        """A direct child module directory is accepted when files are staged."""
        staging_dir = tmp_path / "staging"
        module_dir = staging_dir / "MyTool"
        module_dir.mkdir(parents=True)
        staged_file = module_dir / "MyTool.psm1"
        staged_file.touch()

        result = _resolve_powershell_help_dir(staging_dir, [staged_file], "MyTool")

        assert result == module_dir.resolve()

    def test_returns_none_when_module_dir_missing(self, tmp_path: Path) -> None:
        """A valid module name is ignored when no module directory exists."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        staged_file = staging_dir / "mytool"
        staged_file.touch()

        result = _resolve_powershell_help_dir(staging_dir, [staged_file], "MyTool")

        assert result is None

    @HYPOTHESIS_SETTINGS
    @given(
        ps_module_name=PS_MODULE_NAMES,
        module_dir_exists=st.booleans(),
        staged_under_module=st.booleans(),
    )
    def test_resolution_depends_on_directory_and_staged_file_presence(
        self,
        tmp_path: Path,
        ps_module_name: str,
        *,
        module_dir_exists: bool,
        staged_under_module: bool,
    ) -> None:
        """PowerShell help output requires a real module dir containing a file."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir(exist_ok=True)
        module_dir = staging_dir / ps_module_name
        if module_dir_exists:
            module_dir.mkdir(exist_ok=True)
        staged_file = staging_dir / "mytool"
        if module_dir_exists and staged_under_module:
            staged_file = module_dir / "MyTool.psm1"
        staged_file.touch()

        result = _resolve_powershell_help_dir(
            staging_dir, [staged_file], ps_module_name
        )

        expected = (
            module_dir.resolve() if module_dir_exists and staged_under_module else None
        )
        assert result == expected

    @pytest.mark.parametrize(
        "ps_module_name",
        [
            ".",
            "..",
            "foo/bar",
            "foo\\bar",
            "module/..",
        ],
    )
    def test_rejects_non_segment_module_names(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Non-segment module names never resolve to a help directory."""
        staging_dir = tmp_path / "staging"
        module_dir = staging_dir / "MyTool"
        nested_dir = staging_dir / "foo" / "bar"
        module_dir.mkdir(parents=True)
        nested_dir.mkdir(parents=True)
        staged_paths = [
            staging_dir / "mytool",
            module_dir / "MyTool.psm1",
            nested_dir / "module.psm1",
        ]
        for staged_path in staged_paths:
            staged_path.touch()

        result = _resolve_powershell_help_dir(staging_dir, staged_paths, ps_module_name)

        assert result is None


class TestIsDisallowedPowerShellModuleName:
    """Tests for the _is_disallowed_ps_module_name helper function."""

    @pytest.mark.parametrize("ps_module_name", [".", "..", "foo/bar", "foo\\bar"])
    def test_rejects_invalid_segments(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Special and nested module names are disallowed."""
        staging_root = tmp_path / "staging"
        module_dir = (staging_root / ps_module_name).resolve()

        assert _is_disallowed_ps_module_name(
            staging_root.resolve(), ps_module_name, module_dir
        )

    @HYPOTHESIS_SETTINGS
    @given(ps_module_name=PS_MODULE_NAMES)
    def test_accepts_single_direct_child_names(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Single-segment module names resolve as direct staging children."""
        staging_root = (tmp_path / "staging").resolve()
        module_dir = (staging_root / ps_module_name).resolve()

        assert not _is_disallowed_ps_module_name(
            staging_root, ps_module_name, module_dir
        )

    @HYPOTHESIS_SETTINGS
    @given(
        prefix=PS_MODULE_NAMES,
        suffix=PS_MODULE_NAMES,
        separator=st.sampled_from(("/", "\\")),
    )
    def test_rejects_nested_module_names(
        self, tmp_path: Path, prefix: str, suffix: str, separator: str
    ) -> None:
        """Names containing path separators are rejected."""
        ps_module_name = f"{prefix}{separator}{suffix}"
        staging_root = (tmp_path / "staging").resolve()
        module_dir = (staging_root / ps_module_name).resolve()

        assert _is_disallowed_ps_module_name(staging_root, ps_module_name, module_dir)

    @HYPOTHESIS_SETTINGS
    @given(ps_module_name=PS_MODULE_NAMES)
    def test_rejects_non_direct_children(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Resolved module paths must be direct staging children."""
        staging_root = (tmp_path / "staging").resolve()
        module_dir = (staging_root / "nested" / ps_module_name).resolve()

        assert _is_disallowed_ps_module_name(staging_root, ps_module_name, module_dir)


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

    @HYPOTHESIS_SETTINGS
    @given(
        segments=st.lists(
            st.text(
                alphabet=st.characters(
                    blacklist_characters='/\\\x00:*?"<>|',
                    blacklist_categories=("Cs", "Cc", "Cn"),
                    max_codepoint=0xFFFF,
                ),
                min_size=1,
                max_size=8,
            ).filter(lambda value: value not in {".", ".."}),
            min_size=1,
            max_size=4,
        )
    )
    def test_safe_destination_paths_stay_under_staging_dir(
        self, tmp_path: Path, segments: list[str]
    ) -> None:
        """Generated relative destinations remain under the staging directory."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir(exist_ok=True)
        destination = "/".join(segments)

        result = _safe_destination_path(staging_dir, destination)

        assert result.is_relative_to(staging_dir.resolve())

    @HYPOTHESIS_SETTINGS
    @given(
        destination=st.text(
            alphabet=st.characters(
                blacklist_characters="\x00",
                blacklist_categories=("Cs", "Cc", "Cn"),
                max_codepoint=0xFFFF,
            ),
            min_size=1,
            max_size=12,
        )
    )
    def test_parent_traversal_destinations_are_rejected(
        self, tmp_path: Path, destination: str
    ) -> None:
        """Generated parent traversal destinations cannot escape staging."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir(exist_ok=True)

        with pytest.raises(StageError, match="escapes staging directory"):
            _safe_destination_path(staging_dir, f"../{destination}")
