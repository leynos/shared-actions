"""Container runtime and environment detection helpers."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import typing as typ

import typer
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut
from utils import UnexpectedExecutableError, ensure_allowed_executable, run_validated

if typ.TYPE_CHECKING:
    from pathlib import Path

    from cmd_utils import RunResult

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


def _normalize_arch(machine: str) -> str:
    """Normalize *machine* identifiers to canonical architecture names."""
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


def _default_host_target_for_current_platform(
    *,
    system_name: str | None = None,
    machine: str | None = None,
    sys_platform: str | None = None,
) -> str:
    """Return the default host triple for the provided platform hints."""
    platform_obj = platform
    if machine is None:
        machine_attr = getattr(platform_obj, "machine", None)
        if callable(machine_attr):
            machine = machine_attr()
        elif machine_attr is None:
            machine = os.environ.get("PROCESSOR_ARCHITECTURE", "")
        else:
            machine = machine_attr
    resolved_machine = machine or os.environ.get("PROCESSOR_ARCHITECTURE", "")
    arch = _normalize_arch(resolved_machine)

    if system_name is None:
        system_attr = getattr(platform_obj, "system", None)
        if callable(system_attr):
            system_name = system_attr()
        elif system_attr is None:
            system_name = ""
        else:
            system_name = system_attr
    system = (system_name or "").lower()
    platform_id = (sys_platform or sys.platform or "").lower()

    if system == "windows" or platform_id.startswith("win"):
        return _ARCH_TO_WINDOWS_DEFAULT.get(arch, "x86_64-pc-windows-msvc")

    if system.startswith(("cygwin", "msys")) or platform_id in {"cygwin", "msys"}:
        return f"{arch}-pc-windows-gnu"

    if system == "darwin" or platform_id == "darwin":
        mapped = _ARCH_TO_DARWIN_DEFAULT.get(arch)
        if mapped is not None:
            return mapped
        return f"{arch}-apple-darwin"

    if system.startswith("linux") or platform_id.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"

    if system.startswith("freebsd") or platform_id.startswith("freebsd"):
        return f"{arch}-unknown-freebsd"

    if system.startswith("netbsd") or platform_id.startswith("netbsd"):
        return f"{arch}-unknown-netbsd"

    if system.startswith("openbsd") or platform_id.startswith("openbsd"):
        return f"{arch}-unknown-openbsd"

    identifier = system or platform_id or "linux"
    return f"{arch}-unknown-{identifier}"


def _platform_default_host_target() -> str:
    """Return a platform-specific fallback host triple."""
    return _default_host_target_for_current_platform()


DEFAULT_HOST_TARGET = _platform_default_host_target()
_DEFAULT_PROBE_TIMEOUT = 30
_MAX_PROBE_TIMEOUT = 300


def _run_probe(
    exec_path: str | Path,
    name: str,
    probe: str,
    args: list[str],
    *,
    cwd: str | Path | None = None,
) -> RunResult | None:
    """Execute a runtime probe and handle common failure modes."""
    allowed_names: tuple[str, ...] = (name, f"{name}.exe")
    try:
        return run_validated(
            exec_path,
            args,
            allowed_names=allowed_names,
            method="run",
            cwd=cwd,
            timeout=PROBE_TIMEOUT,
        )
    except ProcessTimedOut:
        typer.echo(
            "::warning:: "
            f"{name} {probe} probe exceeded {PROBE_TIMEOUT}s timeout; "
            "treating runtime as unavailable",
            err=True,
        )
    except (OSError, ProcessExecutionError):
        pass
    return None


def _get_probe_timeout() -> int:
    """Return the sanitized probe timeout for runtime detection."""
    raw = os.environ.get("RUNTIME_PROBE_TIMEOUT")
    if raw is None:
        return _DEFAULT_PROBE_TIMEOUT
    try:
        value = int(raw)
    except ValueError:
        typer.echo(
            "::warning:: Invalid RUNTIME_PROBE_TIMEOUT value"
            f" {raw!r}; using {_DEFAULT_PROBE_TIMEOUT}s fallback",
            err=True,
        )
        return _DEFAULT_PROBE_TIMEOUT
    if value <= 0:
        typer.echo(
            "::warning:: "
            f"RUNTIME_PROBE_TIMEOUT={value}s raised to {_DEFAULT_PROBE_TIMEOUT}s",
            err=True,
        )
        return _DEFAULT_PROBE_TIMEOUT
    if value > _MAX_PROBE_TIMEOUT:
        typer.echo(
            "::warning:: "
            f"RUNTIME_PROBE_TIMEOUT={value}s capped to {_MAX_PROBE_TIMEOUT}s",
            err=True,
        )
        return _MAX_PROBE_TIMEOUT
    return value


PROBE_TIMEOUT = _get_probe_timeout()


def runtime_available(name: str, *, cwd: str | Path | None = None) -> bool:
    """Return True if *name* container runtime is usable."""
    path = shutil.which(name)
    if path is None:
        return False
    try:
        exec_path = ensure_allowed_executable(path, (name, f"{name}.exe"))
    except UnexpectedExecutableError:
        return False
    result = _run_probe(
        exec_path,
        name,
        "info",
        ["info"],
        cwd=cwd,
    )
    if result is None:
        return False

    returncode, _stdout, _stderr = result

    if returncode != 0:
        return False

    if name == "podman":
        security_info = _run_probe(
            exec_path,
            name,
            "security",
            ["info", "--format", "{{json .Host.Security}}"],
            cwd=cwd,
        )
        if security_info is None:
            return False

        try:
            _security_code, security_stdout, _security_stderr = security_info
            security = json.loads(security_stdout or "{}")
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
        _, stdout, _ = typ.cast(
            "tuple[int, str, str]",
            run_validated(
                exec_path,
                ["-vV"],
                allowed_names=("rustc", "rustc.exe"),
                timeout=PROBE_TIMEOUT,
                method="run",
            ),
        )
    except (ProcessExecutionError, ProcessTimedOut, OSError):
        return default

    triple = next(
        (
            line.partition(":")[2].strip()
            for line in (stdout or "").splitlines()
            if line.startswith("host:")
        ),
        "",
    )
    return triple or default
