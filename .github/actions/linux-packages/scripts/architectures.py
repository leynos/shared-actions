"""Architecture detection helpers for linux-packages."""

from __future__ import annotations

import typing as typ


class UnsupportedTargetError(ValueError):
    """Raised when a target triple cannot be mapped to a packaging architecture."""

    def __init__(self, target: str) -> None:
        super().__init__(f"unsupported target triple: {target}")
        self.target = target


class _TargetArch(typ.NamedTuple):
    nfpm: str
    deb: str


_TARGET_MAPPINGS: tuple[tuple[tuple[str, ...], _TargetArch], ...] = (
    (("x86_64-", "x86_64_"), _TargetArch("amd64", "amd64")),
    (
        ("i686-", "i686_", "i586-", "i586_", "i386-", "i386_"),
        _TargetArch("386", "i386"),
    ),
    (("aarch64-", "aarch64_", "arm64-", "arm64_"), _TargetArch("arm64", "arm64")),
    (
        (
            "armv7-",
            "armv7_",
            "armv7l-",
            "armv7l_",
            "armv6-",
            "armv6_",
            "armv6l-",
            "armv6l_",
            "arm-unknown-linux-gnueabihf",
            "arm-unknown-linux-musleabihf",
        ),
        _TargetArch("arm", "armhf"),
    ),
    (("riscv64",), _TargetArch("riscv64", "riscv64")),
    (
        ("powerpc64le-", "powerpc64le_", "ppc64le-", "ppc64le_"),
        _TargetArch("ppc64le", "ppc64el"),
    ),
    (("s390x-", "s390x_"), _TargetArch("s390x", "s390x")),
    (
        ("loongarch64-", "loongarch64_", "loong64-", "loong64_"),
        _TargetArch("loong64", "loong64"),
    ),
)


def _match_target(target: str) -> _TargetArch:
    lowered = target.lower()
    for prefixes, arch in _TARGET_MAPPINGS:
        if lowered.startswith(prefixes):
            return arch
    raise UnsupportedTargetError(target)


def nfpm_arch_for_target(target: str) -> str:
    """Return the nfpm/GOARCH label for *target*."""
    return _match_target(target).nfpm


def deb_arch_for_target(target: str) -> str:
    """Return the Debian architecture label for *target*."""
    return _match_target(target).deb
