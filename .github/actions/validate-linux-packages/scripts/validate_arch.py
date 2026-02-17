"""Architecture mapping and validation utilities for package validation."""

from __future__ import annotations

import platform

__all__ = [
    "_HOST_ARCH_ALIAS_MAP",
    "_host_architectures",
    "_should_skip_sandbox",
    "acceptable_rpm_architectures",
    "rpm_expected_architecture",
]

_HOST_ARCH_ALIAS_MAP: dict[str, set[str]] = {
    "x86_64": {"x86_64", "amd64"},
    "amd64": {"x86_64", "amd64"},
    "aarch64": {"aarch64", "arm64"},
    "arm64": {"aarch64", "arm64"},
    "armv7l": {"armv7l", "armhf"},
    "armv6l": {"armv6l", "armhf"},
    "ppc64le": {"ppc64le"},
    "s390x": {"s390x"},
    "riscv64": {"riscv64"},
    "loongarch64": {"loongarch64", "loong64"},
    "loong64": {"loongarch64", "loong64"},
}


def _host_architectures() -> set[str]:
    """Return aliases for the host processor architecture."""
    machine = (platform.machine() or "").lower()
    if not machine:
        return set()
    aliases = _HOST_ARCH_ALIAS_MAP.get(machine, {machine})
    return {alias.lower() for alias in aliases}


def _should_skip_sandbox(package_architecture: str | None) -> bool:
    """Return ``True`` when sandbox checks should be skipped for the architecture."""
    if not package_architecture:
        return False
    normalized = package_architecture.lower()
    if normalized in {"all", "any", "noarch"}:
        return False

    host_arches = _host_architectures()
    if not host_arches:
        return False
    return normalized not in host_arches


def acceptable_rpm_architectures(arch: str) -> set[str]:
    """Return accepted RPM architecture aliases for nfpm ``arch``."""
    aliases = {
        "amd64": {"amd64", "x86_64"},
        "386": {"386", "i386", "i486", "i586", "i686"},
        "arm": {"arm", "armhfp", "armv7hl"},
        "arm64": {"arm64", "aarch64"},
        "riscv64": {"riscv64"},
        "ppc64le": {"ppc64le"},
        "s390x": {"s390x"},
        "loong64": {"loong64", "loongarch64"},
    }
    return aliases.get(arch, {arch})


def rpm_expected_architecture(arch: str) -> str:
    """Return canonical RPM architecture for nfpm ``arch`` values."""
    return "x86_64" if arch == "amd64" else arch
