"""Shared pytest fixtures for rust-build-release tests."""

from __future__ import annotations

import typing as typ

import pytest
from _packaging_utils import (
    DEFAULT_CONFIG,
    DEFAULT_TARGET,
    BuildArtifacts,
    PackagingConfig,
    PackagingProject,
    build_release_artifacts,
    package_project,
    packaging_project,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


IteratorNone = typ.Iterator[None]


@pytest.fixture
def uncapture_if_verbose(
    request: pytest.FixtureRequest, capfd: pytest.CaptureFixture[str]
) -> IteratorNone:
    """Disable output capture when pytest runs with ``-v`` or higher verbosity."""
    if request.config.get_verbosity() > 0:
        with capfd.disabled():
            yield
    else:
        yield


@pytest.fixture(scope="module")
def packaging_config() -> PackagingConfig:
    """Return the static metadata for the sample packaging project."""
    return DEFAULT_CONFIG


@pytest.fixture(scope="module")
def packaging_target() -> str:
    """Return the Rust target triple used in integration tests."""
    return DEFAULT_TARGET


@pytest.fixture(scope="module")
def packaging_project_paths() -> PackagingProject:
    """Resolve the filesystem layout for packaging integration tests."""
    return packaging_project()


@pytest.fixture(scope="module")
def build_artifacts(
    packaging_project_paths: PackagingProject,
    packaging_target: str,
    packaging_config: PackagingConfig,
) -> BuildArtifacts:
    """Ensure the sample project is built for the requested target."""
    return build_release_artifacts(
        packaging_project_paths,
        packaging_target,
        config=packaging_config,
    )


@pytest.fixture(scope="module")
def packaged_artifacts(
    packaging_project_paths: PackagingProject,
    build_artifacts: BuildArtifacts,
    packaging_config: PackagingConfig,
) -> typ.Mapping[str, Path]:
    """Package the built project as both .deb and .rpm artefacts."""
    return package_project(
        packaging_project_paths,
        build_artifacts,
        config=packaging_config,
        formats=("deb", "rpm"),
    )
