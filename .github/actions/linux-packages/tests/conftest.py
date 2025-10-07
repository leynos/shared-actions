"""Shared fixtures for the linux-packages integration tests."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from _packaging_utils import (
    DEFAULT_CONFIG,
    DEFAULT_TARGET,
    BuildArtifacts,
    PackagingConfig,
    PackagingProject,
    build_release_artifacts,
    clone_packaging_project,
    package_project,
    packaging_project,
)

IteratorNone = typ.Iterator[None]


@pytest.fixture
def uncapture_if_verbose(
    request: pytest.FixtureRequest, capfd: pytest.CaptureFixture[str]
) -> IteratorNone:
    """Disable pytest's output capture when running verbosely."""
    if request.config.get_verbosity() > 0:
        with capfd.disabled():
            yield
    else:
        yield


@pytest.fixture(scope="module")
def packaging_config() -> PackagingConfig:
    """Return static metadata describing the sample project."""
    return DEFAULT_CONFIG


@pytest.fixture(scope="module")
def packaging_target() -> str:
    """Return the target triple used in packaging tests."""
    return DEFAULT_TARGET


@pytest.fixture(scope="module")
def packaging_project_paths(
    tmp_path_factory: pytest.TempPathFactory,
) -> PackagingProject:
    """Resolve filesystem paths required by packaging fixtures."""
    base = packaging_project()
    clone_root = Path(tmp_path_factory.mktemp("packaging-project"))
    return clone_packaging_project(clone_root, base)


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
    """Package the built project for all requested formats."""
    return package_project(
        packaging_project_paths,
        build_artifacts,
        config=packaging_config,
        formats=("deb", "rpm"),
    )
