"""Integration test covering RPM packaging with polythene isolation."""

from __future__ import annotations

import re
import shutil
import sys
import typing as typ

import pytest
from _packaging_utils import (
    HAS_PODMAN_RUNTIME,
    IsolationUnavailableError,
    PackagingProject,
    polythene_rootfs,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

RPM_BASE_IMAGE = "docker.io/library/rockylinux:9"


def _parse_rpm_info(output: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip()] = value.strip()
    return info


@pytest.mark.usefixtures("uncapture_if_verbose")
@pytest.mark.skipif(
    sys.platform == "win32" or not HAS_PODMAN_RUNTIME or shutil.which("uv") is None,
    reason="podman runtime or uv not available",
)
def test_rpm_package_metadata(
    packaging_project_paths: PackagingProject,
    packaged_artifacts: typ.Mapping[str, Path],
) -> None:
    """Build the .rpm package and inspect its metadata inside an isolated rootfs."""
    rpm_package_path = packaged_artifacts.get("rpm")
    assert rpm_package_path is not None, "expected RPM package to be built"

    try:
        with polythene_rootfs(
            packaging_project_paths.polythene_command, RPM_BASE_IMAGE
        ) as rootfs:
            shutil.copy(rpm_package_path, rootfs.root / rpm_package_path.name)
            info_output = rootfs.exec("rpm", "-qip", rpm_package_path.name)
            info = _parse_rpm_info(info_output)
            assert info.get("Name") == "rust-toy-app"
            assert info.get("Version") == "0.1.0"
            release_value = info.get("Release", "")
            assert re.match(r"^1(\.|$)", release_value), release_value
            arch_value = info.get("Architecture")
            assert arch_value in {"amd64", "x86_64", "arm64", "aarch64"}, arch_value

            listing_output = rootfs.exec("rpm", "-qlp", rpm_package_path.name)
            listing = {
                line.strip() for line in listing_output.splitlines() if line.strip()
            }
            assert "/usr/bin/rust-toy-app" in listing
            assert "/usr/share/man/man1/rust-toy-app.1.gz" in listing
    except IsolationUnavailableError as exc:
        pytest.skip(f"isolation unavailable: {exc}")
