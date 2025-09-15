"""E2E packaging test using nFPM and polythene."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import sys

import pytest
from plumbum import local

from cmd_utils import run_cmd


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
        with local.env(CROSS_CONTAINER_ENGINE="docker"):
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
        with tempfile.TemporaryDirectory() as store:
            uid = (
                run_cmd(
                    local["uv"][
                        "run",
                        polythene.as_posix(),
                        "pull",
                        "docker.io/library/debian:bookworm",
                        "--store",
                        store,
                    ]
                )
                .splitlines()[-1]
                .strip()
            )
            try:
                run_cmd(
                    local["uv"][
                        "run",
                        polythene.as_posix(),
                        "exec",
                        uid,
                        "--store",
                        store,
                        "--",
                        "true",
                    ]
                )
            except Exception:
                pytest.skip("isolation unavailable")

            root = Path(store) / uid
            shutil.copy(deb, root / deb.name)
            run_cmd(
                local["uv"][
                    "run",
                    polythene.as_posix(),
                    "exec",
                    uid,
                    "--store",
                    store,
                    "--",
                    "dpkg",
                    "-i",
                    deb.name,
                ]
            )
            run_cmd(
                local["uv"][
                    "run",
                    polythene.as_posix(),
                    "exec",
                    uid,
                    "--store",
                    store,
                    "--",
                    "test",
                    "-x",
                    "/usr/bin/rust-toy-app",
                ]
            )
            run_cmd(
                local["uv"][
                    "run",
                    polythene.as_posix(),
                    "exec",
                    uid,
                    "--store",
                    store,
                    "--",
                    "test",
                    "-f",
                    "/usr/share/man/man1/rust-toy-app.1.gz",
                ]
            )
            result = run_cmd(
                local["uv"][
                    "run",
                    polythene.as_posix(),
                    "exec",
                    uid,
                    "--store",
                    store,
                    "--",
                    "/usr/bin/rust-toy-app",
                ]
            )
            assert "Hello, world!" in result
