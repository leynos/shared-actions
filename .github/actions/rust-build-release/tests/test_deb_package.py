"""E2E packaging test using nFPM and apt."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils import run_cmd


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not available")
def test_deb_package_installs() -> None:
    """Build and install the .deb, verifying binary and man page."""
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    build_script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    pkg_script = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
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
        run_cmd(local[build_script.as_posix()][target])
        man_matches = list(
            project_dir.glob(
                f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
            )
        )
        assert man_matches, "man page not found; ensure 'rust-toy-app.1' is generated"
        man_src = man_matches[0]
        if shutil.which("nfpm") is None:
            url = "https://github.com/goreleaser/nfpm/releases/download/v2.39.0/nfpm_2.39.0_Linux_x86_64.tar.gz"
            with tempfile.TemporaryDirectory() as td:
                tarball = Path(td) / "nfpm.tgz"
                run_cmd(local["curl"]["-sSL", url, "-o", tarball])
                run_cmd(local["tar"]["-xf", tarball, "-C", td])
                run_cmd(
                    local["install"][
                        "-m", "0755", Path(td) / "nfpm", "/usr/local/bin/nfpm"
                    ]
                )
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
        deb = project_dir / "dist/rust-toy-app_0.1.0-1_amd64.deb"
        with local.env(DEBIAN_FRONTEND="noninteractive"):
            try:
                run_cmd(local["dpkg"]["-i", deb.as_posix()])
                assert Path("/usr/bin/rust-toy-app").exists()
                run_cmd(local["man"]["-w", "rust-toy-app"])
                result = run_cmd(local["/usr/bin/rust-toy-app"])
                assert "Hello, world!" in result
            finally:
                try:
                    run_cmd(local["dpkg"]["-r", "rust-toy-app"])
                except ProcessExecutionError:
                    pass
