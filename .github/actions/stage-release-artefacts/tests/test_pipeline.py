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
from stage_common.pipeline import stage_artefacts

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
TEMPLATE_SAFE_PS_MODULE_NAMES = PS_MODULE_NAMES.filter(
    lambda value: "{" not in value and "}" not in value
)
PATH_SEGMENTS = st.text(
    alphabet=st.characters(
        blacklist_characters='/\\\x00:*?"<>|',
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=8,
).filter(lambda value: value not in {".", ".."})
TEMPLATE_SAFE_PATH_SEGMENTS = PATH_SEGMENTS.filter(
    lambda value: "{" not in value and "}" not in value
)
TRAVERSAL_DESTINATIONS = st.text(
    alphabet=st.characters(
        blacklist_characters="\x00",
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=12,
).filter(lambda value: "{" not in value and "}" not in value)
HYPOTHESIS_SETTINGS = settings(
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
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

    @pytest.mark.parametrize(
        "source",
        [
            "{unknown}/x",
            "prefix/{unknown}",
            "{unknown}/{source_name}",
            "{unknown}",
            "dir/{unknown}/x",
            "{target}/{missing}",
            "target/{unknown}/release",
            "{unknown}.txt",
        ],
    )
    def test_invalid_template_key_in_source_raises(
        self, tmp_path: Path, source: str
    ) -> None:
        """Invalid source template keys raise StageError through staging."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = self._make_linux_config(workspace, [ArtefactConfig(source=source)])

        with pytest.raises(StageError, match="Invalid template key"):
            stage_artefacts(config)

    @pytest.mark.parametrize(
        "destination",
        [
            "../../escape/file",
            "../escape/file",
            "nested/../../../escape/file",
            "../file",
            "nested/../../file",
            "nested/deeper/../../../file",
        ],
    )
    def test_path_traversal_destination_raises(
        self, tmp_path: Path, destination: str
    ) -> None:
        """Destination paths escaping the staging directory raise StageError."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "myapp").write_text("binary content", encoding="utf-8")
        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="myapp",
                    destination=destination,
                )
            ],
        )

        with pytest.raises(StageError, match="escapes staging directory"):
            stage_artefacts(config)

    @pytest.mark.parametrize(
        "ps_module_name",
        [
            ".",
            "..",
            "foo/bar",
            "foo\\bar",
        ],
    )
    def test_disallowed_ps_module_names_do_not_set_powershell_help_dir(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Invalid module names are ignored by the public staging API."""
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

        result = stage_artefacts(config, ps_module_name=ps_module_name)

        assert len(result.staged_artefacts) == 4
        assert result.powershell_help_dir is None

    @HYPOTHESIS_SETTINGS
    @given(ps_module_name=TEMPLATE_SAFE_PS_MODULE_NAMES)
    def test_valid_ps_module_names_resolve_when_staged(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Single-segment module names resolve when files are staged under them."""
        workspace = tmp_path / "workspace"
        source_dir = workspace / "powershell" / ps_module_name
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "module.psm1").write_text("module", encoding="utf-8")
        config = StagingConfig(
            workspace=workspace,
            bin_name="mytool",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[
                ArtefactConfig(
                    source=f"powershell/{ps_module_name}/module.psm1",
                    destination=f"{ps_module_name}/module.psm1",
                ),
            ],
            platform="windows",
            arch="x86_64",
            target="x86_64-pc-windows-msvc",
        )

        result = stage_artefacts(config, ps_module_name=ps_module_name)

        assert result.powershell_help_dir == result.staging_dir / ps_module_name

    @HYPOTHESIS_SETTINGS
    @given(
        segments=st.lists(
            TEMPLATE_SAFE_PATH_SEGMENTS,
            min_size=1,
            max_size=4,
        )
    )
    def test_safe_destination_paths_stay_under_staging_dir_through_staging(
        self, tmp_path: Path, segments: list[str]
    ) -> None:
        """Generated relative destinations remain under the staging directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "myapp").write_text("binary content", encoding="utf-8")
        destination = "/".join(segments)
        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="myapp",
                    destination=destination,
                )
            ],
        )

        result = stage_artefacts(config)

        assert result.staged_artefacts[0].is_relative_to(result.staging_dir)

    @HYPOTHESIS_SETTINGS
    @given(destination=TRAVERSAL_DESTINATIONS)
    def test_parent_traversal_destinations_are_rejected_through_staging(
        self, tmp_path: Path, destination: str
    ) -> None:
        """Generated parent traversal destinations cannot escape staging."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "myapp").write_text("binary content", encoding="utf-8")
        config = self._make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="myapp",
                    destination=f"../{destination}",
                )
            ],
        )

        with pytest.raises(StageError, match="escapes staging directory"):
            stage_artefacts(config)
