"""Cranelift codegen-backend detection for the generate-coverage action.

This module provides lightweight text-based detection of the Cranelift
codegen backend in a Cargo project and computes the environment variable
overrides required to force LLVM during coverage runs.

Exported symbols:

- ``_CARGO_COVERAGE_ENV_UNSETS`` — tuple of env-var names to strip from the
  inherited environment before applying coverage overrides.
- ``get_cargo_coverage_env(manifest_path)`` — returns a dict of
  ``CARGO_PROFILE_*_CODEGEN_BACKEND=llvm`` overrides when Cranelift is
  detected; returns an empty dict otherwise.

Detection strategy (in priority order):

1. Search upward from the manifest directory for ``.cargo/config.toml`` or
   ``.cargo/config`` and scan for ``codegen-backend = "cranelift"``.
2. Parse ``[profile.*]`` sections in the given ``Cargo.toml`` for the same
   key.

Known limitation: when ``manifest_path`` points to a workspace member,
profile settings in the workspace-root ``Cargo.toml`` are not scanned.
Use ``.cargo/config.toml`` at the workspace root to ensure detection in
that case.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

_CARGO_COVERAGE_ENV_UNSETS = (
    "CARGO_PROFILE_DEV_CODEGEN_BACKEND",
    "CARGO_PROFILE_TEST_CODEGEN_BACKEND",
)


def _cargo_config_contains_cranelift(candidate: Path) -> bool:
    """Return ``True`` if *candidate* is a cargo config file that sets Cranelift.

    Returns ``False`` on any read or decode error.
    """
    try:
        content = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(
        re.search(
            r'^[ \t]*codegen-backend\s*=\s*["\']cranelift["\']',
            content,
            flags=re.MULTILINE,
        )
    )


def _uses_cranelift_backend(manifest_path: Path) -> bool:
    """Return ``True`` when the project configures the Cranelift codegen backend.

    Searches from the manifest directory upward for ``.cargo/config.toml``
    (or ``.cargo/config``) and checks whether any profile sets
    ``codegen-backend = "cranelift"``.
    """
    search_dir = manifest_path.resolve().parent
    while True:
        for name in ("config.toml", "config"):
            candidate = search_dir / ".cargo" / name
            if candidate.is_file() and _cargo_config_contains_cranelift(candidate):
                return True
        parent = search_dir.parent
        if parent == search_dir:
            break
        search_dir = parent
    return False


def _is_profile_section(section: str) -> bool:
    """Return ``True`` if *section* is a Cargo profile section name.

    Matches both the bare ``[profile]`` table and dotted sub-tables such as
    ``[profile.dev]`` and ``[profile.release]``.
    """
    return section == "profile" or section.startswith("profile.")


def _manifest_uses_cranelift_backend(manifest_path: Path) -> bool:
    """Return ``True`` when ``manifest_path`` configures Cranelift in profiles.

    Parameters
    ----------
    manifest_path : Path
        Path to the ``Cargo.toml`` manifest to inspect.

    Returns
    -------
    bool
        ``True`` if any ``[profile.*]`` section sets
        ``codegen-backend = "cranelift"``; ``False`` otherwise.
    """
    try:
        content = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    in_profile_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        section_match = re.match(r"^\s*\[(?P<section>[^\]]+)\]\s*(?:#.*)?$", line)
        if section_match is not None:
            in_profile_section = _is_profile_section(section_match["section"])
            continue
        if in_profile_section and re.match(
            r"""^codegen-backend\s*=\s*["']cranelift["']""",
            stripped,
        ):
            return True
    return False


def get_cargo_coverage_env(manifest_path: Path) -> dict[str, str]:
    """Return coverage-specific cargo env overrides for Cranelift projects.

    Detects whether the project identified by *manifest_path* uses the
    Cranelift codegen backend (via ``.cargo/config*`` or ``Cargo.toml``
    profile sections). When Cranelift is detected, returns a dict that
    forces LLVM for coverage-instrumented builds. Returns an empty dict
    for non-Cranelift projects.

    Parameters
    ----------
    manifest_path : Path
        Path to the ``Cargo.toml`` manifest for the crate or workspace
        root being instrumented. When this points to a workspace member,
        only that member's manifest is scanned; the workspace-root profile
        is not inspected via this path.

    Returns
    -------
    dict[str, str]
        ``{"CARGO_PROFILE_DEV_CODEGEN_BACKEND": "llvm",
        "CARGO_PROFILE_TEST_CODEGEN_BACKEND": "llvm"}`` when Cranelift is
        detected; ``{}`` otherwise.
    """
    if not _uses_cranelift_backend(
        manifest_path
    ) and not _manifest_uses_cranelift_backend(manifest_path):
        return {}
    return {
        "CARGO_PROFILE_DEV_CODEGEN_BACKEND": "llvm",
        "CARGO_PROFILE_TEST_CODEGEN_BACKEND": "llvm",
    }
