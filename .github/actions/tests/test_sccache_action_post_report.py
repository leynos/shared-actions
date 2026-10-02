"""Contract: every ``mozilla-actions/sccache-action`` use disables its post report.

The sccache action registers a post-job step that runs ``sccache --show-stats``
and fails the job when that errors. After a sccache server fallback the server
is dead and ``--show-stats`` restarts it, so a second startup timeout turns a
lost compiler cache into a red job, which is the failure the fail-open start
exists to prevent. The only switch is the ``disable_annotations`` input, and
despite its name it returns before any statistics call. Statistics are reported
by the caller, with a step guarded on ``sccache-status``.

The scan covers every composite action manifest and workflow in the
repository, not only ``setup-rust``, so a new use anywhere cannot reintroduce
the post step. A step whose value is anything but a literal true does not
count: ``false`` and an expression a caller could set to false would both leave
the report on.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

GITHUB_ROOT = Path(__file__).resolve().parents[2]
SCCACHE_ACTION_PREFIX = "mozilla-actions/sccache-action@"
INPUT = "disable_annotations"


def _step_lists(node: object) -> typ.Iterator[list[object]]:
    """Yield every ``steps`` list in a parsed workflow or action manifest."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "steps" and isinstance(value, list):
                yield value
            yield from _step_lists(value)
    elif isinstance(node, list):
        for item in node:
            yield from _step_lists(item)


def sccache_action_uses(document: object) -> list[dict[str, object]]:
    """Return every step in ``document`` that runs the sccache action."""
    return [
        step
        for steps in _step_lists(document)
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and str(step["uses"]).startswith(SCCACHE_ACTION_PREFIX)
    ]


def leaves_post_report_on(step: dict[str, object]) -> bool:
    """Report whether ``step`` fails to switch the post report off."""
    inputs = step.get("with")
    value = inputs.get(INPUT) if isinstance(inputs, dict) else None
    return not (value is True or value == "true")


def _manifests() -> list[Path]:
    """Return every workflow and composite action manifest in the repository."""
    return sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in GITHUB_ROOT.rglob(pattern)
        if "node_modules" not in path.parts
    )


def _uses_in_repository() -> list[tuple[Path, dict[str, object]]]:
    """Return each sccache-action use with the file that declares it."""
    found: list[tuple[Path, dict[str, object]]] = []
    for path in _manifests():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        found.extend((path, step) for step in sccache_action_uses(document))
    return found


def test_the_repository_still_uses_the_sccache_action() -> None:
    """A scan over nothing passes whatever it checks, so require some uses.

    ``setup-rust`` runs the action twice, once for the x86_64 macOS pin and
    once for the rest.
    """
    uses = _uses_in_repository()
    assert len(uses) >= 2, (
        f"expected setup-rust's two sccache-action uses, found {len(uses)}; "
        "if the action is gone, retire this contract"
    )


def test_every_sccache_action_use_disables_the_post_report() -> None:
    """No use may leave the post-job ``--show-stats`` step to fail a fallback."""
    offenders = [
        f"{path.relative_to(GITHUB_ROOT)}: {step.get('name', step.get('uses'))}"
        for path, step in _uses_in_repository()
        if leaves_post_report_on(step)
    ]
    assert not offenders, (
        f"these sccache-action uses must set {INPUT}: true, otherwise the post "
        f"step restarts a dead server and can fail the job: {offenders}"
    )


@pytest.mark.parametrize(
    ("with_block", "expected"),
    [
        ({"version": "v0.17.0"}, True),
        ({INPUT: False}, True),
        ({INPUT: "false"}, True),
        ({INPUT: "${{ inputs.quiet }}"}, True),
        (None, True),
        ({INPUT: True}, False),
        ({INPUT: "true"}, False),
    ],
)
def test_only_a_literal_true_counts_as_disabled(
    with_block: dict[str, object] | None, *, expected: bool
) -> None:
    """False, a missing block and an expression all leave the report on."""
    step: dict[str, object] = {"uses": f"{SCCACHE_ACTION_PREFIX}abc"}
    if with_block is not None:
        step["with"] = with_block
    assert leaves_post_report_on(step) is expected


def test_a_new_use_without_the_input_is_found_in_a_workflow() -> None:
    """The scan reads workflow jobs as well as composite action manifests."""
    document = yaml.safe_load(
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: mozilla-actions/sccache-action@abc\n"
        "      - uses: mozilla-actions/sccache-action@abc\n"
        "        with:\n"
        "          disable_annotations: true\n"
    )
    uses = sccache_action_uses(document)
    assert len(uses) == 2
    assert [leaves_post_report_on(step) for step in uses] == [True, False]
