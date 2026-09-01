"""Regression coverage for third-party actions' supported Node runtimes."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NODE24_ACTION_REVISIONS = {
    "actions/cache": "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "actions/upload-artifact": (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    ),
    "mozilla-actions/sccache-action": (
        "mozilla-actions/sccache-action@fc920bf0ec8de6ee65d409111f7ec508035751ba"
    ),
}


def _workflow_documents() -> typ.Iterator[tuple[Path, object]]:
    """Yield parsed workflow and composite-action YAML documents."""
    for path in sorted((REPOSITORY_ROOT / ".github").rglob("*.yml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _uses_references(value: object) -> typ.Iterator[str]:
    """Yield every GitHub Actions ``uses`` reference from a YAML value."""
    if isinstance(value, dict):
        uses = value.get("uses")
        if isinstance(uses, str):
            yield uses
        for nested_value in value.values():
            yield from _uses_references(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            yield from _uses_references(nested_value)


def test_affected_actions_use_node24_immutable_revisions() -> None:
    """Cache, artefact, and sccache actions retain supported pinned runtimes."""
    unexpected_references: list[str] = []
    for path, document in _workflow_documents():
        for uses in _uses_references(document):
            action = uses.partition("@")[0]
            expected = NODE24_ACTION_REVISIONS.get(action)
            if expected is not None and uses != expected:
                unexpected_references.append(
                    f"{path.relative_to(REPOSITORY_ROOT)}: {uses}"
                )

    assert not unexpected_references, "\n".join(unexpected_references)
