"""Tests for stage-release-artefacts staging pipeline."""

from __future__ import annotations

import tarfile
import typing as typ
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pytest_bdd import given as bdd_given
from pytest_bdd import parsers, scenario, then, when
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.config import ArtefactConfig, BinstallConfig, StagingConfig
from stage_common.pipeline import (
    StageResult,
    _validate_archive_member_name,
    stage_artefacts,
)

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
        blacklist_characters='/\\\x00:*?"<>|',
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
ARCHIVE_MEMBER_NAMES = st.text(
    alphabet=st.characters(
        blacklist_characters="/\\\0",
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=40,
).filter(lambda value: value not in {".", ".."})


def _assert_path_traversal_rejected(tmp_path: Path, destination: str) -> None:
    """Arrange a minimal workspace and assert StageError for escaping destination."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "myapp").write_text("binary content", encoding="utf-8")

    config = TestStageArtefacts._make_linux_config(
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
        assert result.skipped_artefacts == ["optional"]

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

    def test_creates_binstall_archive(self, tmp_path: Path) -> None:
        """Enabled cargo-binstall config creates an archive and checksum."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "1.2.3"\n',
            encoding="utf-8",
        )
        release_dir = workspace / "target/x86_64-unknown-linux-gnu/release"
        release_dir.mkdir(parents=True)
        (release_dir / "myapp").write_text("binary content", encoding="utf-8")
        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[ArtefactConfig(source="target/{target}/release/{bin_name}")],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            binstall=BinstallConfig(enabled=True),
        )

        result = stage_artefacts(config)

        archive = result.staging_dir / "myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz"
        assert archive.exists()
        assert (archive.with_name(f"{archive.name}.sha256")).exists()
        with tarfile.open(archive, "r:gz") as package:
            assert package.getnames() == ["myapp"]
            member = package.extractfile("myapp")
            assert member is not None
            assert member.read() == b"binary content"
        assert result.outputs["binstall_archive_path"] == archive
        assert archive.name in [path.name for path in result.staged_artefacts]

    def test_binstall_archive_resolves_workspace_version(self, tmp_path: Path) -> None:
        """Workspace-inherited Cargo versions are supported."""
        workspace = tmp_path / "workspace"
        crate_dir = workspace / "crates/myapp"
        crate_dir.mkdir(parents=True)
        (workspace / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/myapp"]\n'
            '[workspace.package]\nversion = "2.0.0"\n',
            encoding="utf-8",
        )
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion.workspace = true\n',
            encoding="utf-8",
        )
        release_dir = workspace / "target/x86_64-unknown-linux-gnu/release"
        release_dir.mkdir(parents=True)
        (release_dir / "myapp").write_text("binary content", encoding="utf-8")
        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[ArtefactConfig(source="target/{target}/release/{bin_name}")],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            binstall=BinstallConfig(
                enabled=True,
                manifest_path="crates/myapp/Cargo.toml",
            ),
        )

        result = stage_artefacts(config)

        assert (
            result.staging_dir / "myapp-2.0.0-x86_64-unknown-linux-gnu.tar.gz"
        ).exists()

    def test_binstall_explicit_metadata_takes_precedence(self, tmp_path: Path) -> None:
        """Explicit binstall metadata avoids Cargo manifest values."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(
            '[package]\nname = "manifest-name"\nversion = "0.0.1"\n',
            encoding="utf-8",
        )
        release_dir = workspace / "target/x86_64-unknown-linux-gnu/release"
        release_dir.mkdir(parents=True)
        (release_dir / "cli").write_text("binary content", encoding="utf-8")
        config = StagingConfig(
            workspace=workspace,
            bin_name="cli",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            binstall=BinstallConfig(
                enabled=True,
                package_name="configured-name",
                version="9.9.9",
                bin_name="cli",
            ),
        )

        result = stage_artefacts(config)

        assert (
            result.staging_dir / "configured-name-9.9.9-x86_64-unknown-linux-gnu.tar.gz"
        ).exists()

    def test_binstall_archive_requires_source_binary(self, tmp_path: Path) -> None:
        """Missing cargo-binstall binary source raises StageError."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "1.2.3"\n',
            encoding="utf-8",
        )
        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            binstall=BinstallConfig(enabled=True),
        )

        with pytest.raises(StageError, match="cargo-binstall binary source"):
            stage_artefacts(config)

    def test_binstall_checksum_map_includes_archive(self, tmp_path: Path) -> None:
        """Archive digests are included in the checksum map."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "1.2.3"\n',
            encoding="utf-8",
        )
        release_dir = workspace / "target/x86_64-unknown-linux-gnu/release"
        release_dir.mkdir(parents=True)
        (release_dir / "myapp").write_text("binary content", encoding="utf-8")
        config = StagingConfig(
            workspace=workspace,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            binstall=BinstallConfig(enabled=True),
        )

        result = stage_artefacts(config)

        assert "myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz" in result.checksums

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

    @staticmethod
    def _make_windows_config(
        workspace: Path,
        artefacts: list[ArtefactConfig] | None = None,
    ) -> StagingConfig:
        """Return a Windows StagingConfig for PowerShell sidecar tests."""
        artefacts_to_use = (
            artefacts
            if artefacts is not None
            else TestStageArtefacts._powershell_artefact_configs()
        )
        return StagingConfig(
            workspace=workspace,
            bin_name="mytool",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=artefacts_to_use,
            platform="windows",
            arch="x86_64",
            target="x86_64-pc-windows-msvc",
        )

    def test_stages_windows_powershell_help_dir(self, tmp_path: Path) -> None:
        """Windows PowerShell sidecars are staged under the module directory."""
        workspace, _ = self._make_powershell_workspace(tmp_path)

        config = self._make_windows_config(workspace)

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

        config = self._make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source=(
                        "target/orthohelp/{target}/release/powershell/"
                        "MyTool/MyTool.psm1"
                    ),
                    destination="MyTool/MyTool.psm1",
                ),
            ],
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

        config = self._make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source="powershell/MyTool/MyTool.psm1",
                    destination="MyTool/MyTool.psm1",
                ),
            ],
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
        _assert_path_traversal_rejected(tmp_path, destination)

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
        config = self._make_windows_config(workspace)

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
        config = self._make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source=f"powershell/{ps_module_name}/module.psm1",
                    destination=f"{ps_module_name}/module.psm1",
                ),
            ],
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
        _assert_path_traversal_rejected(tmp_path, f"../{destination}")


class TestValidateArchiveMemberName:
    """Tests for cargo-binstall archive member path validation."""

    @HYPOTHESIS_SETTINGS
    @given(member_name=ARCHIVE_MEMBER_NAMES)
    def test_accepts_simple_relative_file_names(self, member_name: str) -> None:
        """Simple relative member names are accepted."""
        assert _validate_archive_member_name(member_name) == member_name

    @pytest.mark.parametrize(
        "member_name",
        ["", "/myapp", "../myapp", "nested/../myapp", "myapp/"],
    )
    def test_rejects_unsafe_member_names(self, member_name: str) -> None:
        """Unsafe archive member names are rejected."""
        with pytest.raises(StageError):
            _validate_archive_member_name(member_name)


@pytest.fixture
def bdd_context(tmp_path: Path) -> dict[str, object]:
    """Return mutable context for pytest-bdd steps."""
    return {"workspace": tmp_path / "workspace", "target": ""}


@scenario(
    "features/binstall_archive.feature",
    "staging a Linux target creates a cargo-binstall archive",
)
def test_binstall_archive_feature() -> None:
    """Run the cargo-binstall archive staging behaviour scenario."""


@bdd_given(
    parsers.parse(
        'a workspace with a Cargo package named "{name}" at version "{version}"'
    )
)
def bdd_workspace_with_cargo_package(
    bdd_context: dict[str, object], name: str, version: str
) -> None:
    """Create a Cargo package manifest for the BDD scenario."""
    workspace = typ.cast("Path", bdd_context["workspace"])
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    bdd_context["bin_name"] = name


@bdd_given(parsers.parse('a release binary for target "{target}"'))
def bdd_release_binary(bdd_context: dict[str, object], target: str) -> None:
    """Create a release binary for the BDD scenario."""
    workspace = typ.cast("Path", bdd_context["workspace"])
    bin_name = typ.cast("str", bdd_context["bin_name"])
    release_dir = workspace / "target" / target / "release"
    release_dir.mkdir(parents=True)
    (release_dir / bin_name).write_text("binary content", encoding="utf-8")
    bdd_context["target"] = target


@bdd_given("stage-release-artefacts has cargo-binstall archive creation enabled")
def bdd_binstall_enabled(bdd_context: dict[str, object]) -> None:
    """Configure cargo-binstall archive creation for the BDD scenario."""
    workspace = typ.cast("Path", bdd_context["workspace"])
    bin_name = typ.cast("str", bdd_context["bin_name"])
    target = typ.cast("str", bdd_context["target"])
    bdd_context["config"] = StagingConfig(
        workspace=workspace,
        bin_name=bin_name,
        dist_dir="dist",
        checksum_algorithm="sha256",
        artefacts=[ArtefactConfig(source="target/{target}/release/{bin_name}")],
        platform="linux",
        arch="x86_64",
        target=target,
        target_key="linux-x86_64",
        binstall=BinstallConfig(enabled=True),
    )


@when(parsers.parse('the staging action runs for target "{target_key}"'))
def bdd_stage_runs(bdd_context: dict[str, object], target_key: str) -> None:
    """Run stage_artefacts for the BDD scenario."""
    config = typ.cast("StagingConfig", bdd_context["config"])
    assert config.target_key == target_key
    bdd_context["result"] = stage_artefacts(config)


@then(parsers.parse('the staged files include "{archive_name}"'))
def bdd_staged_files_include_archive(
    bdd_context: dict[str, object], archive_name: str
) -> None:
    """Assert the expected archive was staged."""
    result = typ.cast("StageResult", bdd_context["result"])
    archive = result.staging_dir / archive_name
    assert archive.exists()
    bdd_context["archive"] = archive


@then(parsers.parse('the archive contains "{member_name}" at the root'))
def bdd_archive_contains_member(
    bdd_context: dict[str, object], member_name: str
) -> None:
    """Assert the archive contains the expected root-level binary."""
    archive = typ.cast("Path", bdd_context["archive"])
    with tarfile.open(archive, "r:gz") as package:
        assert package.getnames() == [member_name]


@then("a SHA-256 sidecar exists for the archive")
def bdd_archive_checksum_exists(bdd_context: dict[str, object]) -> None:
    """Assert the archive checksum sidecar exists."""
    archive = typ.cast("Path", bdd_context["archive"])
    assert archive.with_name(f"{archive.name}.sha256").exists()


@then(parsers.parse('the GitHub output includes "{output_name}"'))
def bdd_output_includes_archive_path(
    bdd_context: dict[str, object], output_name: str
) -> None:
    """Assert the staging result contains the binstall archive output key."""
    result = typ.cast("StageResult", bdd_context["result"])
    assert output_name in result.outputs
