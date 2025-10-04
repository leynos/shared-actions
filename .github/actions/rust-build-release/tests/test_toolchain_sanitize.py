"""Verify toolchain triple is sanitized for cross."""

from __future__ import annotations

import os
import platform
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut

if typ.TYPE_CHECKING:
    from types import ModuleType

from cmd_utils import run_cmd


class RunResult(typ.NamedTuple):
    """Container for script execution results."""

    returncode: int
    stdout: str
    stderr: str


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


def run_script(script: Path, *args: str, cwd: Path | None = None) -> RunResult:
    """Execute *script* in *cwd* and return the run result."""
    command = local[str(script)]
    if args:
        command = command[list(args)]
    try:
        code, stdout, stderr = typ.cast(
            "tuple[int, str | bytes, str | bytes]",
            run_cmd(
                command,
                method="run",
                cwd=cwd,
            ),
        )
        return RunResult(
            code,
            stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout,
            stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr,
        )
    except (  # pragma: no cover - defensive path
        OSError,
        ProcessExecutionError,
        ProcessTimedOut,
        ValueError,
    ) as exc:
        fallback_cmd = (str(script), *args)
        return RunResult(1, "", f"{fallback_cmd}: {exc}")


def _host_linux_triple() -> str:
    """Return the host's GNU/Linux target triple."""
    if not sys.platform.startswith("linux"):  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported platform: {sys.platform!r}")

    machine = platform.machine().lower()
    triple_map = {
        "x86_64": "x86_64-unknown-linux-gnu",
        "amd64": "x86_64-unknown-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
        "arm64": "aarch64-unknown-linux-gnu",
        "i686": "i686-unknown-linux-gnu",
        "i586": "i686-unknown-linux-gnu",
        "i386": "i686-unknown-linux-gnu",
        "riscv64": "riscv64-unknown-linux-gnu",
        "ppc64le": "powerpc64le-unknown-linux-gnu",
        "ppc64": "powerpc64-unknown-linux-gnu",
        "ppc64be": "powerpc64-unknown-linux-gnu",
        "s390x": "s390x-unknown-linux-gnu",
        "armv8l": "armv7-unknown-linux-gnueabihf",
        "armv7l": "armv7-unknown-linux-gnueabihf",
        "armv7": "armv7-unknown-linux-gnueabihf",
        "armv7a": "armv7-unknown-linux-gnueabihf",
        "armv7hl": "armv7-unknown-linux-gnueabihf",
        "armv7hnl": "armv7-unknown-linux-gnueabihf",
        "armhf": "armv7-unknown-linux-gnueabihf",
        "armv6l": "arm-unknown-linux-gnueabihf",
        "armv6": "arm-unknown-linux-gnueabihf",
        "armel": "arm-unknown-linux-gnueabi",
    }
    triple = triple_map.get(machine)
    if triple is None:  # pragma: no cover - defensive skip
        pytest.skip(f"unsupported architecture: {machine!r}")
    return triple


@pytest.mark.usefixtures("uncapture_if_verbose")
def test_accepts_toolchain_with_triple() -> None:
    """Running with a full toolchain triple succeeds."""
    target_triple = _host_linux_triple()
    toolchain_spec = f"{RUST_TOOLCHAIN}-{target_triple}"
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    run_cmd(
        local["rustup"][
            "toolchain",
            "install",
            RUST_TOOLCHAIN,
            "--profile",
            "minimal",
            "--no-self-update",
        ]
    )
    # Ensure the host-qualified toolchain name exists as well
    # (no-op if already present).
    run_cmd(
        local["rustup"][
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
