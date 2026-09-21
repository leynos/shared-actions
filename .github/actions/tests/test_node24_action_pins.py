"""Regression coverage for third-party actions' supported Node runtimes."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NODE24_ACTION_REVISIONS = {
    "actions/cache": "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "actions/cache/restore": (
        "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    ),
    "actions/cache/save": (
        "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    ),
    "actions/upload-artifact": (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    ),
    "mozilla-actions/sccache-action": (
        "mozilla-actions/sccache-action@fc920bf0ec8de6ee65d409111f7ec508035751ba"
    ),
}


def _workflow_documents() -> typ.Iterator[tuple[Path, object]]:
    """Yield parsed workflow and composite-action YAML documents."""
    for path in sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in (REPOSITORY_ROOT / ".github").rglob(pattern)
    ):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _uses_references(value: object) -> typ.Iterator[str]:
    """Yield every GitHub Actions ``uses`` reference from a YAML value."""
    if isinstance(value, dict):
        yield from _mapping_uses_references(value)
    elif isinstance(value, list):
        yield from _iterable_uses_references(value)


def _mapping_uses_references(value: dict[object, object]) -> typ.Iterator[str]:
    """Yield ``uses`` references in a mapping and its values."""
    uses = value.get("uses")
    if isinstance(uses, str):
        yield uses
    yield from _iterable_uses_references(value.values())


def _iterable_uses_references(values: typ.Iterable[object]) -> typ.Iterator[str]:
    """Yield ``uses`` references in nested YAML values."""
    for value in values:
        yield from _uses_references(value)


def test_workflow_documents_scan_both_yaml_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow discovery includes both supported YAML file extensions."""
    github_root = tmp_path / ".github"
    github_root.mkdir()
    expected_paths = [github_root / "a.yaml", github_root / "z.yml"]
    for path in expected_paths:
        path.write_text("name: test\n", encoding="utf-8")

    monkeypatch.setitem(globals(), "REPOSITORY_ROOT", tmp_path)

    assert [path for path, _ in _workflow_documents()] == expected_paths


def test_uses_references_traverses_nested_yaml_values() -> None:
    """Nested maps and lists yield only string-valued action references."""
    value = {
        "uses": ["ignored", {"uses": "nested-action@revision"}],
        "jobs": [{"uses": 42, "steps": [{"uses": "list-action@revision"}]}],
    }

    assert list(_uses_references(value)) == [
        "nested-action@revision",
        "list-action@revision",
    ]


@pytest.mark.parametrize(
    "action",
    ["actions/cache", "actions/cache/restore", "actions/cache/save"],
)
def test_affected_actions_reject_mismatched_cache_revisions(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct and sub-action cache references retain their approved revisions."""
    uses = f"{action}@mutable"
    path = REPOSITORY_ROOT / ".github" / "node24-pin.yml"
    monkeypatch.setitem(
        globals(),
        "_workflow_documents",
        lambda: iter(((path, {"uses": uses}),)),
    )

    with pytest.raises(AssertionError) as error:
        test_affected_actions_use_node24_immutable_revisions()

    relative_path = path.relative_to(REPOSITORY_ROOT)
    assert f"{relative_path}: {uses}" in str(error.value)


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
