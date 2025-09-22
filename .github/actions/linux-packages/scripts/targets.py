#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9.0,<3.0",
# ]
# ///
"""Resolve Linux target triples to packaging metadata."""

from __future__ import annotations

import dataclasses
import sys
import typing as typ

import cyclopts
from cyclopts import App, Parameter

_APP_CONFIG = {"config": (cyclopts.config.Env("INPUT_", command=False),)}
app = App(**typ.cast("dict[str, typ.Any]", _APP_CONFIG))


@dataclasses.dataclass(frozen=True, slots=True)
class TargetInfo:
    """Description of a target triple and associated packaging labels."""

    triple: str
    platform: str
    nfpm_arch: str
    staging_arch: str
    deb_arch: str


class TargetResolutionError(RuntimeError):
    """Raised when a target triple cannot be mapped to known metadata."""

    def __init__(self, target: str) -> None:
        super().__init__(f"unsupported target triple: {target}")


_BASE_TARGETS: tuple[tuple[tuple[str, ...], TargetInfo], ...] = (
    (
        ("x86_64-", "x86_64_"),
        TargetInfo("", "linux", "amd64", "amd64", "amd64"),
    ),
    (
        ("aarch64-", "arm64-"),
        TargetInfo("", "linux", "arm64", "arm64", "arm64"),
    ),
    (
        ("i686-", "i586-", "i386-"),
        TargetInfo("", "linux", "386", "i386", "i386"),
    ),
    (
        ("armv7-", "armv6-", "arm-"),
        TargetInfo("", "linux", "arm", "armhf", "armhf"),
    ),
    (
        ("riscv64-", "riscv64gc-"),
        TargetInfo("", "linux", "riscv64", "riscv64", "riscv64"),
    ),
    (
        ("powerpc64le-", "ppc64le-"),
        TargetInfo("", "linux", "ppc64le", "ppc64le", "ppc64le"),
    ),
    (
        ("s390x-",),
        TargetInfo("", "linux", "s390x", "s390x", "s390x"),
    ),
    (
        ("loongarch64-", "loong64-"),
        TargetInfo("", "linux", "loong64", "loong64", "loong64"),
    ),
)


def resolve_target(target: str) -> TargetInfo:
    """Return :class:`TargetInfo` for *target* or raise ``TargetResolutionError``."""
    candidate = target.strip()
    lowered = candidate.lower()
    for prefixes, info in _BASE_TARGETS:
        if any(lowered.startswith(prefix) for prefix in prefixes):
            return dataclasses.replace(info, triple=candidate)
    raise TargetResolutionError(target)


_FIELD_GETTERS: dict[str, typ.Callable[[TargetInfo], str]] = {
    "platform": lambda info: info.platform,
    "nfpm-arch": lambda info: info.nfpm_arch,
    "staging-arch": lambda info: info.staging_arch,
    "deb-arch": lambda info: info.deb_arch,
    "triple": lambda info: info.triple,
}

FieldName = typ.Literal["platform", "nfpm-arch", "staging-arch", "deb-arch", "triple"]
OutputFormat = typ.Literal["plain", "env"]


@app.default
def main(
    *,
    target: typ.Annotated[str, Parameter(required=True)],
    field: typ.Annotated[list[FieldName] | None, Parameter(name="field")] = None,
    output_format: OutputFormat = "plain",
) -> None:
    """Print metadata for *target* in the requested format."""
    info = resolve_target(target)
    requested = field or ["staging-arch"]
    values: list[str] = []
    normalised: list[str] = []

    for item in requested:
        key = item.lower()
        getter = _FIELD_GETTERS.get(key)
        if getter is None:  # pragma: no cover - defensive, choices guard above
            print(f"unsupported field: {item}", file=sys.stderr)
            raise SystemExit(2)
        values.append(str(getter(info)))
        normalised.append(key)

    if output_format == "env":
        for key, value in zip(normalised, values, strict=False):
            name = key.upper().replace("-", "_")
            print(f"{name}={value}")
    else:
        print(" ".join(values))


__all__ = ["TargetInfo", "TargetResolutionError", "resolve_target"]


if __name__ == "__main__":
    app()
