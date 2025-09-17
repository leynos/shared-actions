"""E2E packaging test using nFPM and polythene."""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_cmd
from _packaging_utils import (
    IsolationUnavailableError,
    ensure_nfpm,
    polythene_rootfs,
)

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
from script_utils import unique_match

def deb_arch_for_target(target: str) -> str:
    """Return the nfpm architecture label for *target*.

    Parameters
    ----------
    target : str
        Rust target triple used for the build.

    Returns
    -------
    str
        Architecture token recognised by nfpm.
    """
    lowered = target.lower()
    if lowered.startswith(("x86_64-", "x86_64_")):
        return "amd64"
    if lowered.startswith(("aarch64-", "arm64-")):
        return "arm64"
    return "amd64"


@pytest.mark.usefixtures("uncapture_if_verbose")
@pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("dpkg-deb") is None
    or shutil.which("podman") is None,
    reason="dpkg-deb or podman not available",
)
def test_deb_package_installs() -> None:
    """Build the .deb, install in an isolated rootfs, and verify binary and man page."""
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    build_script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    pkg_script = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
    polythene = Path(__file__).resolve().parents[1] / "scripts" / "polythene.py"
    target = "x86_64-unknown-linux-gnu"
    with local.cwd(project_dir):
        run_cmd(
            local["rustup"][
                "toolchain",
                "install",
                "1.89.0",
                "--profile",
                "minimal",
                "--no-self-update",
            ]
        )
        with local.env(CROSS_CONTAINER_ENGINE="podman"):
            run_cmd(local[sys.executable][build_script.as_posix(), target])
        man_src = unique_match(
            project_dir.glob(
                f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
            ),
            description="rust-toy-app man page",
        )
        with ensure_nfpm(project_dir):
            run_cmd(
                local["uv"][
                    "run",
                    pkg_script.as_posix(),
                    "--name",
                    "rust-toy-app",
                    "--bin-name",
                    "rust-toy-app",
                    "--target",
                    target,
                    "--version",
                    "0.1.0",
                    "--formats",
                    "deb",
                    "--man",
                    man_src.as_posix(),
                ]
            )
        deb_arch = deb_arch_for_target(target)
        deb = project_dir / f"dist/rust-toy-app_0.1.0-1_{deb_arch}.deb"
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
                assert "Architecture: amd64" in control_txt
        try:
            with polythene_rootfs(
                polythene, "docker.io/library/debian:bookworm"
            ) as rootfs:
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
