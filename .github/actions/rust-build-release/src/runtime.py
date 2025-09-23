"""Container runtime and environment detection helpers."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import typing as typ

import typer
from utils import UnexpectedExecutableError, ensure_allowed_executable, run_validated

if typ.TYPE_CHECKING:
    from pathlib import Path

CROSS_CONTAINER_ERROR_CODES = {125, 126, 127}
_ARCH_TO_WINDOWS_DEFAULT = {
    "amd64": "x86_64-pc-windows-msvc",
    "x86_64": "x86_64-pc-windows-msvc",
    "arm64": "aarch64-pc-windows-msvc",
    "aarch64": "aarch64-pc-windows-msvc",
}

_ARCH_TO_DARWIN_DEFAULT = {
    "x86_64": "x86_64-apple-darwin",
    "amd64": "x86_64-apple-darwin",
    "arm64": "aarch64-apple-darwin",
    "aarch64": "aarch64-apple-darwin",
}


def _platform_default_host_target() -> str:
    """Return a platform-specific fallback host triple."""
    machine = (
        platform.machine().lower()
        or os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    )
    if sys_platform := sys.platform:
        if sys_platform == "win32":
            return _ARCH_TO_WINDOWS_DEFAULT.get(machine, "x86_64-pc-windows-msvc")
        if sys_platform == "darwin":
            return _ARCH_TO_DARWIN_DEFAULT.get(machine, "x86_64-apple-darwin")
    return "x86_64-unknown-linux-gnu"


DEFAULT_HOST_TARGET = _platform_default_host_target()
PROBE_TIMEOUT = int(os.environ.get("RUNTIME_PROBE_TIMEOUT", "10"))


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
            timeout=PROBE_TIMEOUT,
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
                timeout=PROBE_TIMEOUT,
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
            timeout=PROBE_TIMEOUT,
        )
    except (
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
