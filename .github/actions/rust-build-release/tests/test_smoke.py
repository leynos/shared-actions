"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cmd_utils import run_cmd

os.environ.setdefault("CROSS_CONTAINER_ENGINE", "docker")

if sys.platform == "win32":
    pytest.skip("cross build not supported on Windows runners", allow_module_level=True)

engine = os.environ.get("CROSS_CONTAINER_ENGINE")
container_available = (
    shutil.which(engine) is not None
    if engine
    else (shutil.which("docker") is not None or shutil.which("podman") is not None)
)
if engine and not container_available:
    pytest.skip(
        f"CROSS_CONTAINER_ENGINE={engine} specified but not found",
        allow_module_level=True,
    )
_CROSS_RUNTIME_UNAVAILABLE = not container_available


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
@pytest.mark.parametrize(
    "target",
    [
        "x86_64-unknown-linux-gnu",
        pytest.param(
            "aarch64-unknown-linux-gnu",
            marks=pytest.mark.skipif(
                _CROSS_RUNTIME_UNAVAILABLE,
                reason="container runtime required for cross build",
            ),
        ),
    ],
)
def test_action_builds_release_binary_and_manpage(target: str) -> None:
    """The build script produces a release binary and man page."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    existing = subprocess.run(
        ["rustup", "toolchain", "list"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if "1.89.0" not in existing:
        run_cmd(["rustup", "toolchain", "install", "1.89.0", "--profile", "minimal"])
    res = run_script(script, target, cwd=project_dir)
    if res.returncode != 0:
        err_out = ((res.stderr or "") + (res.stdout or "")).lower()
        if (
            "operation not permitted" in err_out
            or "exit status 126" in err_out
            or "container runtime" in err_out
            or "permission denied" in err_out
            or "exec format error" in err_out
        ):
            pytest.skip("container runtime cannot run cross build in this environment")
    assert res.returncode == 0
    binary = project_dir / f"target/{target}/release/rust-toy-app"
    assert binary.exists()
    manpage_glob = project_dir.glob(
        f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)


def test_fails_without_target() -> None:
    """Script exits with an error when no target is provided."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    res = run_script(script, cwd=project_dir)
    assert res.returncode != 0
    assert "RBR_TARGET=<unset>" in res.stderr


def test_fails_for_invalid_toolchain() -> None:
    """Script surfaces rustup errors for invalid toolchains."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = Path(__file__).resolve().parents[4] / "rust-toy-app"
    res = run_script(
        script,
        "x86_64-unknown-linux-gnu",
        "--toolchain",
        "bogus",
        cwd=project_dir,
    )
    assert res.returncode != 0
    assert "toolchain 'bogus' is not installed" in res.stderr
