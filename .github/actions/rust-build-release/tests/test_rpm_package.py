from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_cmd
from _packaging_utils import ensure_nfpm, polythene_cmd, polythene_exec

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
from script_utils import unique_match  # noqa: E402


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
    sys.platform == "win32" or shutil.which("podman") is None,
    reason="podman not available",
)
def test_rpm_package_metadata() -> None:
    """Build the .rpm package and inspect its metadata inside an isolated rootfs."""
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
        ensure_nfpm(project_dir)
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
                "rpm",
                "--man",
                man_src.as_posix(),
            ]
        )
        rpm_path = unique_match(
            project_dir.glob("dist/rust-toy-app-0.1.0-1*.rpm"),
            description="rust-toy-app rpm",
        )

        with tempfile.TemporaryDirectory() as store:
            uid = (
                polythene_cmd(
                    polythene,
                    "pull",
                    "docker.io/library/rockylinux:9",
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
            shutil.copy(rpm_path, root / rpm_path.name)
            info_output = polythene_exec(
                polythene,
                uid,
                store,
                "rpm",
                "-qip",
                rpm_path.name,
            )
            info = _parse_rpm_info(info_output)
            assert info.get("Name") == "rust-toy-app"
            assert info.get("Version") == "0.1.0"
            release_value = info.get("Release", "")
            assert release_value.startswith("1"), release_value
            arch_value = info.get("Architecture")
            assert arch_value in {"amd64", "x86_64"}, arch_value

            listing_output = polythene_exec(
                polythene,
                uid,
                store,
                "rpm",
                "-qlp",
                rpm_path.name,
            )
            listing = {line.strip() for line in listing_output.splitlines() if line.strip()}
            assert "/usr/bin/rust-toy-app" in listing
            assert "/usr/share/man/man1/rust-toy-app.1.gz" in listing
