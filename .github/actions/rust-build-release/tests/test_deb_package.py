"""E2E packaging test using nFPM and polythene."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_cmd

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
from script_utils import unique_match


def polythene_cmd(polythene: Path, *args: str) -> str:
    return run_cmd(local["uv"]["run", polythene.as_posix(), *args])


def polythene_exec(polythene: Path, uid: str, store: str, *cmd: str) -> str:
    return polythene_cmd(polythene, "exec", uid, "--store", store, "--", *cmd)


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
        if shutil.which("nfpm") is None:
            version = "v2.39.0"
            host = run_cmd(local["uname"]["-m"]).strip()
            arch_map = {"x86_64": "x86_64", "aarch64": "arm64"}
            asset_arch = arch_map.get(host, "x86_64")
            url = (
                "https://github.com/goreleaser/nfpm/releases/download/"
                f"{version}/nfpm_{version[1:]}_Linux_{asset_arch}.tar.gz"
            )
            tools_dir = project_dir / "dist" / ".tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory() as td:
                tarball = Path(td) / "nfpm.tgz"
                run_cmd(local["curl"]["-sSL", url, "-o", tarball])
                run_cmd(local["tar"]["-xzf", tarball, "-C", td, "nfpm"])
                run_cmd(
                    local["install"][
                        "-m", "0755", Path(td) / "nfpm", tools_dir / "nfpm"
                    ]
                )
            path = os.environ.get("PATH", "")
            prefix = f"{tools_dir.as_posix()}:"
            if not path.startswith(prefix):
                os.environ["PATH"] = f"{prefix}{path}"
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
        with tempfile.TemporaryDirectory() as store:
            uid = (
                polythene_cmd(
                    polythene,
                    "pull",
                    "docker.io/library/debian:bookworm",
                    "--store",
                    store,
                )
                .splitlines()[-1]
                .strip()
            )
            try:
                polythene_exec(polythene, uid, store, "true")
            except Exception:
                pytest.skip("isolation unavailable")

            root = Path(store) / uid
            shutil.copy(deb, root / deb.name)
            polythene_exec(polythene, uid, store, "dpkg", "-i", deb.name)
            try:
                polythene_exec(
                    polythene,
                    uid,
                    store,
                    "test",
                    "-x",
                    "/usr/bin/rust-toy-app",
                )
                polythene_exec(
                    polythene,
                    uid,
                    store,
                    "test",
                    "-f",
                    "/usr/share/man/man1/rust-toy-app.1.gz",
                )
                result = polythene_exec(
                    polythene,
                    uid,
                    store,
                    "/usr/bin/rust-toy-app",
                )
                assert "Hello, world!" in result, f"unexpected output: {result!r}"
            finally:
                with contextlib.suppress(Exception):
                    polythene_exec(
                        polythene,
                        uid,
                        store,
                        "dpkg",
                        "-r",
                        "rust-toy-app",
                    )
