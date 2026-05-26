"""Behaviour tests for cargo-binstall archive staging."""

from __future__ import annotations

import tarfile
import typing as typ
from pathlib import Path  # noqa: TC003

from pytest_bdd import given as bdd_given
from pytest_bdd import parsers, scenario, then, when
from stage_common.config import ArtefactConfig, BinstallConfig, StagingConfig
from stage_common.pipeline import StageResult, stage_artefacts


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
