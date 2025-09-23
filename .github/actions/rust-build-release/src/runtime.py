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


def _normalize_arch(machine: str) -> str:
    mapping = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "i386": "i686",
        "i486": "i686",
        "i586": "i686",
        "i686": "i686",
        "x86": "i686",
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "armv8": "aarch64",
        "armv8a": "aarch64",
        "armv8l": "aarch64",
        "armv7": "armv7",
        "armv7a": "armv7",
        "armv7hl": "armv7",
        "armv7l": "armv7",
        "armv6": "armv6",
        "armv6l": "armv6",
        "ppc64": "ppc64",
        "ppc64le": "ppc64le",
        "powerpc64": "ppc64",
        "powerpc64le": "ppc64le",
        "s390x": "s390x",
        "riscv64": "riscv64",
        "loongarch64": "loongarch64",
    }
    if not machine:
        return "x86_64"
    machine_lower = machine.lower()
    return mapping.get(machine_lower, machine_lower)


def _default_host_target_for_current_platform() -> str:
    arch = _normalize_arch(platform.machine()) or "x86_64"
    system_name = platform.system().lower()
    platform_id = sys.platform.lower()
    if system_name == "windows":
        return f"{arch}-pc-windows-msvc"
    if system_name.startswith(("cygwin", "msys")) or platform_id in {"cygwin", "msys"}:
        return f"{arch}-pc-windows-gnu"
    if system_name == "darwin":
        return f"{arch}-apple-darwin"
    if system_name.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"
    identifier = system_name or platform_id or "linux"
    return f"{arch}-unknown-{identifier}"


DEFAULT_HOST_TARGET = _default_host_target_for_current_platform()
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
