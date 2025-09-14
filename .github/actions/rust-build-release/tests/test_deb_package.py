"""E2E packaging test using GoReleaser and apt."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from plumbum import local

from cmd_utils import run_cmd


@pytest.mark.skipif(shutil.which("apt") is None, reason="apt not available")
def test_deb_package_installs() -> None:
    """Build, package, and install the .deb."""
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    target = "x86_64-unknown-linux-gnu"
    with local.cwd(project_dir):
        existing = local["rustup"]["toolchain", "list"].run()[1]
        if "1.89.0" not in existing:
            run_cmd(
                local["rustup"][
                    "toolchain", "install", "1.89.0", "--profile", "minimal"
                ]
            )
        run_cmd(local[script.as_posix()][target])
        dist = project_dir / "dist" / "rust-toy-app_linux_amd64"
        dist.mkdir(parents=True, exist_ok=True)
        bin_path = project_dir / f"target/{target}/release/rust-toy-app"
        shutil.copy(bin_path, dist)
        man_src = next(
            project_dir.glob(
                f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
            )
        )
        shutil.copy(man_src, dist)
        pkg = project_dir / "pkg"
        (pkg / "DEBIAN").mkdir(parents=True, exist_ok=True)
        control = pkg / "DEBIAN/control"
        control.write_text(
            """Package: rust-toy-app
Version: 0.1.0
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Example <ops@example.com>
Description: Toy application for release pipeline tests
"""
        )
        (pkg / "usr/bin").mkdir(parents=True, exist_ok=True)
        shutil.copy(bin_path, pkg / "usr/bin/rust-toy-app")
        (pkg / "usr/share/man/man1").mkdir(parents=True, exist_ok=True)
        shutil.copy(man_src, pkg / "usr/share/man/man1/rust-toy-app.1")
        run_cmd(
            local["dpkg-deb"][
                "--build",
                pkg,
                project_dir / "dist/rust-toy-app_0.1.0_amd64.deb",
            ]
        )
        deb = project_dir / "dist/rust-toy-app_0.1.0_amd64.deb"
        with local.env(DEBIAN_FRONTEND="noninteractive"):
            run_cmd(local["apt"]["install", "-y", str(deb)])
            try:
                assert Path("/usr/bin/rust-toy-app").exists()
                run_cmd(local["man"]["-w", "rust-toy-app"])
                output = run_cmd(local["/usr/bin/rust-toy-app"])
                assert "Hello, world!" in output
            finally:
                run_cmd(local["apt"]["remove", "-y", "rust-toy-app"])
