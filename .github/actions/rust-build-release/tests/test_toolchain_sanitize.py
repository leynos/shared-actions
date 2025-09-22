"""Verify toolchain triple is sanitized for cross."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from cmd_utils import run_cmd

os.environ.setdefault("CROSS_CONTAINER_ENGINE", "docker")

if sys.platform == "win32":
    pytest.skip("cross build not supported on Windows runners", allow_module_level=True)


def _host_linux_triple() -> str:
    """Return the host's GNU/Linux target triple."""
    if sys.platform != "linux":  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported platform: {sys.platform!r}")

    machine = platform.machine().lower()
    triple_map = {
        "x86_64": "x86_64-unknown-linux-gnu",
        "amd64": "x86_64-unknown-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
        "arm64": "aarch64-unknown-linux-gnu",
        "armv7l": "armv7-unknown-linux-gnueabihf",
        "armv6l": "arm-unknown-linux-gnueabihf",
        "i686": "i686-unknown-linux-gnu",
        "riscv64": "riscv64gc-unknown-linux-gnu",
        "ppc64le": "powerpc64le-unknown-linux-gnu",
        "s390x": "s390x-unknown-linux-gnu",
        "loongarch64": "loongarch64-unknown-linux-gnu",
    }
    triple = triple_map.get(machine)
    if triple is None:  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported architecture: {machine!r}")
    return triple


def run_script(
    script: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute *script* in *cwd* and return the completed process."""
    cmd = [str(script), *args]
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            check=False,
        )
    except (  # pragma: no cover - defensive path
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


@pytest.mark.usefixtures("uncapture_if_verbose")
def test_accepts_toolchain_with_triple() -> None:
    """Running with a full toolchain triple succeeds."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    host_triple = _host_linux_triple()
    toolchain = f"1.89.0-{host_triple}"
    run_cmd(
        [
            "rustup",
            "toolchain",
            "install",
            "--profile",
            "minimal",
            "--no-self-update",
            toolchain,
        ]
    )
    res = run_script(
        script,
        host_triple,
        "--toolchain",
        toolchain,
        cwd=project_dir,
    )
    assert res.returncode == 0
    binary = project_dir / f"target/{host_triple}/release/rust-toy-app"
    assert binary.exists()
    manpage_glob = project_dir.glob(
        f"target/{host_triple}/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)
