"""Tests for cargo-binstall archive creation and archive member name validation."""

from __future__ import annotations

import dataclasses
import tarfile
from pathlib import Path  # noqa: TC003

import pytest
from hypothesis import given
from stage_common import StageError
from stage_common.config import ArtefactConfig, BinstallConfig, StagingConfig
from stage_common.pipeline import (  # noqa: F401
    StageResult,
    _validate_archive_member_name,
    stage_artefacts,
)

from conftest import ARCHIVE_MEMBER_NAMES, HYPOTHESIS_SETTINGS


@dataclasses.dataclass(frozen=True)
class BinstallWorkspaceSpec:
    """Parameter object describing a minimal binstall workspace for testing."""

    cargo_name: str
    cargo_version: str
    bin_name: str
    binstall: BinstallConfig
    target: str = "x86_64-unknown-linux-gnu"


def _make_binstall_config(tmp_path: Path, spec: BinstallWorkspaceSpec) -> StagingConfig:
    """Set up a minimal binstall workspace and return its StagingConfig."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text(
        f'[package]\nname = "{spec.cargo_name}"\nversion = "{spec.cargo_version}"\n',
        encoding="utf-8",
    )
    release_dir = workspace / f"target/{spec.target}/release"
    release_dir.mkdir(parents=True)
    (release_dir / spec.bin_name).write_text("binary content", encoding="utf-8")
    return StagingConfig(
        workspace=workspace,
        bin_name=spec.bin_name,
        dist_dir="dist",
        checksum_algorithm="sha256",
        artefacts=[],
        platform="linux",
        arch="x86_64",
        target=spec.target,
        binstall=spec.binstall,
    )


class TestStageArtefactsBinstall:
    """Tests for cargo-binstall archive creation."""

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
        config = _make_binstall_config(
            tmp_path,
            BinstallWorkspaceSpec(
                cargo_name="manifest-name",
                cargo_version="0.0.1",
                bin_name="cli",
                binstall=BinstallConfig(
                    enabled=True,
                    package_name="configured-name",
                    version="9.9.9",
                    bin_name="cli",
                ),
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
        config = _make_binstall_config(
            tmp_path,
            BinstallWorkspaceSpec(
                cargo_name="myapp",
                cargo_version="1.2.3",
                bin_name="myapp",
                binstall=BinstallConfig(enabled=True),
            ),
        )

        result = stage_artefacts(config)

        assert "myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz" in result.checksums


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
