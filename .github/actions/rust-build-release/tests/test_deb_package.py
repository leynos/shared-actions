"""E2E packaging test using GoReleaser and dpkg-deb."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import tempfile

import pytest
from plumbum import local

from cmd_utils import run_cmd


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not available")
def test_deb_package_installs() -> None:
    """Build and validate the .deb without root."""
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
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
        run_cmd(local[script.as_posix()][target])
        dist = project_dir / "dist" / "rust-toy-app_linux_amd64"
        dist.mkdir(parents=True, exist_ok=True)
        bin_path = project_dir / f"target/{target}/release/rust-toy-app"
        shutil.copy(bin_path, dist)
        man_matches = list(
            project_dir.glob(
                f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
            )
        )
        assert man_matches, "man page not found; ensure 'rust-toy-app.1' is generated"
        man_src = man_matches[0]
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
        with tempfile.TemporaryDirectory() as td:
            run_cmd(local["dpkg-deb"]["-x", str(deb), td])
            bin_extracted = Path(td, "usr/bin/rust-toy-app")
            man_extracted = Path(td, "usr/share/man/man1/rust-toy-app.1")
            assert bin_extracted.exists()
            assert os.access(bin_extracted, os.X_OK)
            assert man_extracted.exists()
            output = run_cmd(local[str(bin_extracted)])
            assert "Hello, world!" in output
