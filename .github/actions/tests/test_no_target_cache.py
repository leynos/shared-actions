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


CACHE_ACTION_PREFIXES = ("actions/cache@", "actions/cache/")


def _is_cache_step(step: dict[str, object]) -> bool:
    """Report whether ``step`` invokes ``actions/cache`` or its save/restore forms."""
    uses = step.get("uses")
    return isinstance(uses, str) and uses.startswith(CACHE_ACTION_PREFIXES)


def _cache_path_block(step: dict[str, object]) -> str | None:
    """Return the ``with.path`` block of ``step``, or ``None`` if it has none."""
    step_inputs = step.get("with")
    if not isinstance(step_inputs, dict):
        return None
    paths = step_inputs.get("path")
    return paths if isinstance(paths, str) else None


def _path_entries(block: str) -> cabc.Iterator[str]:
    """Yield the non-empty, stripped path entries of a cache ``path`` block."""
    return (entry for entry in map(str.strip, block.splitlines()) if entry)


def _cache_path_entries(manifest_path: Path) -> cabc.Iterator[tuple[str, str]]:
    """Yield ``(step name, path entry)`` pairs for every archive-cache step."""
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cache_steps = (step for step in manifest["runs"]["steps"] if _is_cache_step(step))
    for step in cache_steps:
        block = _cache_path_block(step)
        if block is None:
            continue
        step_name = str(step.get("name", "<unnamed step>"))
        yield from ((step_name, entry) for entry in _path_entries(block))


def _is_target_entry(entry: str) -> bool:
    """Report whether ``entry`` archives the Cargo ``target`` tree.

    Matching is by whole path segment, so ``./target`` and
    ``${{ github.workspace }}/target`` are caught while unrelated names such as
    ``target-manifests`` are not. A segment beginning ``target${`` catches a
    reintroduced ``target/${{ ... }}`` placeholder.
    """
    return any(
        segment == "target" or segment.startswith("target${")
        for segment in entry.replace("\\", "/").split("/")
    )


@pytest.mark.parametrize(
    "entry",
    [
        "target",
        "./target",
        "target/debug",
        "target/${{ env.BUILD_PROFILE }}",
        "${{ github.workspace }}/target",
        "workspace/target/llvm-cov-target",
    ],
)
def test_is_target_entry_rejects_archived_target_trees(entry: str) -> None:
    """Every form that archives a ``target`` tree is rejected."""
    assert _is_target_entry(entry)


@pytest.mark.parametrize(
    "entry",
    [
        "~/.cargo/registry",
        "~/.cargo/git",
        "~/.cargo/bin/cargo-nextest",
        "target-manifests",
        "${{ env.NIGHTLY_SYSROOT }}/lib/rustlib/x86_64-unknown-openbsd",
    ],
)
def test_is_target_entry_accepts_unrelated_paths(entry: str) -> None:
    """Paths that merely resemble ``target`` remain cacheable."""
    assert not _is_target_entry(entry)


@pytest.mark.parametrize(
    "uses",
    [
        "actions/cache@v4",
        "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
        "actions/cache/restore@v4",
        "actions/cache/save@v4",
    ],
)
def test_is_cache_step_selects_archive_cache_steps(uses: str) -> None:
    """Every ``actions/cache`` form contributes path entries to the contract."""
    assert _is_cache_step({"uses": uses})


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "actions/upload-artifact@v4"},
        {"uses": "mozilla-actions/sccache-action@v0.0.9"},
        {"run": "cargo build"},
    ],
)
def test_is_cache_step_ignores_other_steps(step: dict[str, object]) -> None:
    """A non-cache step's ``path`` input is not an archive-cache entry."""
    assert not _is_cache_step(step)


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
