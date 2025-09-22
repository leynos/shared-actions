"""Verify toolchain triple is sanitized for cross."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cmd_utils import run_cmd


def test_toolchain_channel_strips_host_triple(main_module: ModuleType) -> None:
    """The action strips host triples when preparing cross CLI overrides."""

    channel = main_module._toolchain_channel("1.89.0-x86_64-unknown-linux-gnu")
    assert channel == "1.89.0"

    nightly = main_module._toolchain_channel(
        "nightly-2024-08-10-x86_64-unknown-linux-gnu"
    )
    assert nightly == "nightly-2024-08-10"

    stable = main_module._toolchain_channel("stable")
    assert stable == "stable"

os.environ.setdefault("CROSS_CONTAINER_ENGINE", "docker")

RUST_TOOLCHAIN = os.getenv("RUST_TOOLCHAIN", "1.89.0")

if sys.platform == "win32":
    pytest.skip("cross build not supported on Windows runners", allow_module_level=True)


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


def _host_linux_triple() -> str:
    """Return the host's GNU/Linux target triple."""
    if not sys.platform.startswith("linux"):  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported platform: {sys.platform!r}")

    machine = platform.machine().lower()
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "riscv64": "riscv64",
        "ppc64le": "powerpc64le",
        "ppc64": "powerpc64",
        "ppc64be": "powerpc64",
        "s390x": "s390x",
    }
    arch = arch_map.get(machine)
    if arch is None:  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported architecture: {machine!r}")
    return f"{arch}-unknown-linux-gnu"


@pytest.mark.usefixtures("uncapture_if_verbose")
def test_accepts_toolchain_with_triple() -> None:
    """Running with a full toolchain triple succeeds."""
    target_triple = _host_linux_triple()
    toolchain_spec = f"{RUST_TOOLCHAIN}-{target_triple}"
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    run_cmd(
        [
            "rustup",
            "toolchain",
            "install",
            RUST_TOOLCHAIN,
            "--profile",
            "minimal",
            "--no-self-update",
        ]
    )
    # Ensure the host-qualified toolchain name exists as well (no-op if already present).
    run_cmd(
        [
            "rustup",
            "toolchain",
            "install",
            toolchain_spec,
            "--profile",
            "minimal",
            "--no-self-update",
        ]
    )
    res = run_script(
        script,
        target_triple,
        "--toolchain",
        toolchain_spec,
        cwd=project_dir,
    )
    assert res.returncode == 0
    binary = project_dir / f"target/{target_triple}/release/rust-toy-app"
    assert binary.exists()
    manpage_glob = project_dir.glob(
        f"target/{target_triple}/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)
