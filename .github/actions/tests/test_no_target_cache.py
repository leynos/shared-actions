"""Contract tests forbidding ``target`` trees in Rust action cache inputs.

The ``setup-rust`` and ``generate-coverage`` actions deliberately leave the
Cargo ``target`` tree uncached: sccache is the sole owner of compiler output.
These tests fail if a ``target`` path is reintroduced into either manifest's
``actions/cache`` inputs.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

ACTIONS_ROOT = Path(__file__).resolve().parents[1]
RUST_ACTION_MANIFESTS = (
    ACTIONS_ROOT / "setup-rust" / "action.yml",
    ACTIONS_ROOT / "generate-coverage" / "action.yml",
)
FORBIDDEN_PREFIXES = ("target/", "target${")


def _cache_path_entries(
    manifest_path: Path,
) -> cabc.Iterator[tuple[str, str]]:
    """Yield ``(step name, path entry)`` pairs for every cache-like step."""
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    for step in manifest["runs"]["steps"]:
        step_inputs = step.get("with")
        if not isinstance(step_inputs, dict):
            continue
        paths = step_inputs.get("path")
        if not isinstance(paths, str):
            continue
        step_name = str(step.get("name", "<unnamed step>"))
        for line in paths.splitlines():
            entry = line.strip()
            if entry:
                yield step_name, entry


def _is_target_entry(entry: str) -> bool:
    """Report whether ``entry`` archives the Cargo ``target`` tree."""
    return entry == "target" or entry.startswith(FORBIDDEN_PREFIXES)


@pytest.mark.parametrize(
    "manifest_path", RUST_ACTION_MANIFESTS, ids=lambda path: path.parent.name
)
def test_cache_steps_do_not_archive_target(manifest_path: Path) -> None:
    """Neither Rust action may archive the ``target`` tree."""
    offenders = [
        f"{manifest_path.parent.name}: step {step_name!r} caches {entry!r}"
        for step_name, entry in _cache_path_entries(manifest_path)
        if _is_target_entry(entry)
    ]

    assert not offenders, (
        "sccache owns compiler output; cache steps must not archive the "
        "Cargo target tree. Offending entries: " + "; ".join(offenders)
    )


@pytest.mark.parametrize(
    "manifest_path", RUST_ACTION_MANIFESTS, ids=lambda path: path.parent.name
)
def test_cache_steps_still_archive_the_cargo_registry(manifest_path: Path) -> None:
    """Guard against the contract passing because every cache step vanished."""
    entries = {entry for _, entry in _cache_path_entries(manifest_path)}

    assert "~/.cargo/registry" in entries, (
        f"{manifest_path.parent.name} must still archive the Cargo registry; "
        f"found {sorted(entries)}"
    )
