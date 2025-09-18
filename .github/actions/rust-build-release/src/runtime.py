"""Container runtime and environment detection helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
import typing as typ

import typer
from utils import UnexpectedExecutableError, ensure_allowed_executable, run_validated

if typ.TYPE_CHECKING:
    from pathlib import Path

CROSS_CONTAINER_ERROR_CODES = {125, 126, 127}
DEFAULT_HOST_TARGET = "x86_64-unknown-linux-gnu"


def runtime_available(name: str, *, cwd: str | Path | None = None) -> bool:
    """Return True if *name* container runtime is usable."""
    path = shutil.which(name)
    if path is None:
        return False
    try:
        exec_path = ensure_allowed_executable(path, (name, f"{name}.exe"))
    except UnexpectedExecutableError:
        return False
    try:
        result = run_validated(
            exec_path,
            ["info"],
            allowed_names=(name, f"{name}.exe"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False

    if name == "podman":
        try:
            security_info = run_validated(
                exec_path,
                ["info", "--format", "{{json .Host.Security}}"],
                allowed_names=(name, f"{name}.exe"),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
                cwd=cwd,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

        try:
            security = json.loads(security_info.stdout or "{}")
        except json.JSONDecodeError:
            return False

        raw_caps = security.get("capabilities")
        if isinstance(raw_caps, str):
            caps = {cap.strip().upper() for cap in raw_caps.split(",") if cap.strip()}
        elif isinstance(raw_caps, list):
            caps = {str(cap).upper() for cap in raw_caps}
        else:
            caps = set()

        if "CAP_SYS_ADMIN" not in caps:
            typer.echo(
                "::warning:: podman missing CAP_SYS_ADMIN; treating runtime as "
                "unavailable",
                err=True,
            )
            return False

    return True


def detect_host_target(
    *,
    default: str = DEFAULT_HOST_TARGET,
    rustc_path: str | Path | None = None,
) -> str:
    """Return the active Rust host triple, defaulting to *default* when unknown."""
    candidate = rustc_path or shutil.which("rustc")
    if candidate is None:
        return default
    try:
        exec_path = ensure_allowed_executable(candidate, ("rustc", "rustc.exe"))
    except UnexpectedExecutableError:
        return default
    try:
        result = run_validated(
            exec_path,
            ["-vV"],
            allowed_names=("rustc", "rustc.exe"),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ):
        return default

    triple = next(
        (
            line.partition(":")[2].strip()
            for line in (result.stdout or "").splitlines()
            if line.startswith("host:")
        ),
        "",
    )
    return triple or default
