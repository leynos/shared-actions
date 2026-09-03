"""Repository-wide contract for `actions/cache` references.

Every composite action must reach `actions/cache`, or one of its `restore` and
`save` sub-actions, through a full commit SHA. A moving tag such as `@v4`
breaks the repository's pinning policy, and the older releases it can resolve
to are not intercepted by a transparent runner cache, so their saves become
wasted upload on the runners this estate uses.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTIONS_ROOT = Path(__file__).resolve().parents[1]

#: A pinned reference: the action path, then a full 40-character commit SHA.
PINNED_REFERENCE = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _action_manifests() -> list[Path]:
    """Return every composite action manifest in the repository.

    Recursive, so a manifest nested under an action's own directory cannot sit
    outside the contract. The sweep is only as good as its reach.
    """
    manifests = sorted(
        path
        for suffix in ("action.yml", "action.yaml")
        for path in ACTIONS_ROOT.rglob(suffix)
    )
    assert manifests, "no action manifests found"
    return manifests


def _is_cache_reference(uses: str) -> bool:
    """Return whether *uses* names `actions/cache` or one of its sub-actions."""
    return uses.split("@", 1)[0].split("/")[:2] == ["actions", "cache"]


def _cache_references(manifest: Path) -> list[str]:
    """Return every `actions/cache` reference declared in *manifest*."""
    loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    steps: list[dict[str, typ.Any]] = loaded.get("runs", {}).get("steps", []) or []
    return [
        uses
        for step in steps
        if isinstance(uses := step.get("uses"), str) and _is_cache_reference(uses)
    ]


@pytest.mark.parametrize(
    "manifest",
    _action_manifests(),
    ids=lambda manifest: str(manifest.relative_to(ACTIONS_ROOT).parent),
)
def test_cache_references_are_sha_pinned(manifest: Path) -> None:
    """No action may reach `actions/cache` through a floating tag."""
    unpinned = [
        uses
        for uses in _cache_references(manifest)
        if not PINNED_REFERENCE.fullmatch(uses)
    ]

    relative = manifest.relative_to(ACTIONS_ROOT)
    assert not unpinned, f"{relative} has unpinned references: {unpinned}"


def test_every_action_shares_one_cache_revision() -> None:
    """One revision across the repository, so a bump is a single decision.

    Divergent pins would let one action silently keep an older release after
    the next bump, which is the state this contract exists to prevent.
    """
    revisions = {
        uses.split("@", 1)[1]
        for manifest in _action_manifests()
        for uses in _cache_references(manifest)
    }

    assert len(revisions) == 1, f"actions pin differing cache revisions: {revisions}"


def test_the_contract_covers_the_actions_that_use_the_cache() -> None:
    """Guard the discovery itself, so an empty sweep cannot pass silently."""
    users = {
        manifest.parent.name
        for manifest in _action_manifests()
        if _cache_references(manifest)
    }

    assert {
        "generate-coverage",
        "install-whitaker",
        "ratchet-coverage",
        "setup-rust",
        "upload-codescene-coverage",
    } <= users
