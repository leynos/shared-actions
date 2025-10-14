"""E2E packaging test using nFPM and polythene."""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
import typing as typ
from pathlib import Path

import pytest
from _packaging_utils import (
    HAS_PODMAN_RUNTIME,
    BuildArtifacts,
    IsolationUnavailableError,
    PackagingProject,
    deb_arch_for_target,
    polythene_rootfs,
)
from plumbum import local

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd


@pytest.mark.usefixtures("uncapture_if_verbose")
@pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("dpkg-deb") is None
    or not HAS_PODMAN_RUNTIME
    or shutil.which("uv") is None,
    reason="dpkg-deb, podman runtime or uv not available",
)
def test_deb_package_installs(
    packaging_project_paths: PackagingProject,
    build_artifacts: BuildArtifacts,
    packaged_artifacts: typ.Mapping[str, Path],
) -> None:
    """Build the .deb, install in an isolated rootfs, and verify binary and man page."""
    polythene = packaging_project_paths.polythene_command
    deb = packaged_artifacts.get("deb")
    assert deb is not None, "expected Debian package to be built"
    deb_arch = deb_arch_for_target(build_artifacts.target)
    with tempfile.TemporaryDirectory() as td:
        run_cmd(local["dpkg-deb"]["-x", str(deb), td])
        bin_extracted = Path(td, "usr/bin/rust-toy-app")
        man_extracted = Path(td, "usr/share/man/man1/rust-toy-app.1.gz")
        assert bin_extracted.is_file()
        assert man_extracted.is_file()
        with tempfile.TemporaryDirectory() as cd:
            run_cmd(local["dpkg-deb"]["-e", str(deb), cd])
            control_txt = Path(cd, "control").read_text(encoding="utf-8")
            assert "Package: rust-toy-app" in control_txt
            assert f"Architecture: {deb_arch}" in control_txt
    try:
        with polythene_rootfs(polythene, "docker.io/library/debian:bookworm") as rootfs:
            shutil.copy(deb, rootfs.root / deb.name)
            rootfs.exec("dpkg", "-i", deb.name)
            try:
                rootfs.exec("test", "-x", "/usr/bin/rust-toy-app")
                rootfs.exec("test", "-f", "/usr/share/man/man1/rust-toy-app.1.gz")
                result = rootfs.exec("/usr/bin/rust-toy-app")
                assert "Hello, world!" in result, f"unexpected output: {result!r}"
            finally:
                with contextlib.suppress(Exception):
                    rootfs.exec("dpkg", "-r", "rust-toy-app")
    except IsolationUnavailableError as exc:
        pytest.skip(
            "podman-based isolation unavailable (polythene exec failed). "
            f"Ensure rootless Podman is installed and operational. Details: {exc}"
        )
