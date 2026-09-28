"""Refusal cases for the coverage lanes: pull requests ratchet, main writes.

Each case changes one thing in the compliant fixture tree and asserts on
the one rule that must refuse it, so deleting that rule's clause fails
the case.
"""

from __future__ import annotations

import pytest

from contract_fixtures import (
    PULL_REQUEST_LANE,
    REPOSITORY,
    mutate,
    parse_tree,
    tree,
)
from cv005_contracts.lanes import (
    interpreter_violations,
    publisher_lane_violations,
    pull_request_lane_violations,
    second_writer_violations,
)
from cv005_contracts.loading import Document, load_workflow


def _documents(texts: dict[str, str]) -> dict[str, Document]:
    """Parse a tree of texts."""
    return {name: load_workflow(text) for name, text in texts.items()}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("          with-ratchet: 'true'\n", ""),
        ("          publish-artefact: 'false'\n", ""),
        # GitHub passes `yes` and `no` to the action as strings.
        ("          with-ratchet: 'true'\n", "          with-ratchet: yes\n"),
        ("          publish-artefact: 'false'\n", "          publish-artefact: no\n"),
    ],
)
def test_a_pull_request_lane_ratchets_and_publishes_nothing(old: str, new: str) -> None:
    """A lane without the ratchet, or publishing its report, is refused."""
    documents = _documents(mutate("ci.yml", old, new))
    found = pull_request_lane_violations({"ci.yml": documents["ci.yml"]})
    assert found, found


@pytest.mark.parametrize(
    "guard",
    [
        "",
        "        if: always()\n",
        "        if: github.event_name == 'pull_request' || always()\n",
        "        if: ${{ !(github.event_name == 'pull_request') }}\n",
        "        if: github.event_name == 'pull_request' && ${{ true }}\n",
    ],
)
def test_a_push_lane_cannot_write_a_second_baseline(guard: str) -> None:
    """Coverage on a push outside the publisher is refused."""
    texts = mutate("ci.yml", "        if: github.event_name == 'pull_request'\n", guard)
    found = second_writer_violations(_documents(texts), "coverage-main.yml", REPOSITORY)
    assert found, found


def test_a_job_level_pull_request_guard_is_accepted() -> None:
    """A job guarded to pull requests never runs on a push, nor do its steps."""
    texts = mutate(
        "ci.yml",
        "    runs-on: ubuntu-latest\n",
        "    runs-on: ubuntu-latest\n    if: github.event_name == 'pull_request'\n",
    )
    texts["ci.yml"] = texts["ci.yml"].replace(
        "        if: github.event_name == 'pull_request'\n", ""
    )
    found = second_writer_violations(_documents(texts), "coverage-main.yml", REPOSITORY)
    assert not found, found


@pytest.mark.parametrize(
    ("guard", "expected"),
    [
        ("    if: github.event_name == 'pull_request'\n", False),
        ("", True),
    ],
)
def test_a_call_from_a_guarded_job_is_not_a_push_writer(
    guard: str, *, expected: bool
) -> None:
    """A callee reached only through a pull-request job never runs on a push."""
    caller = (
        "on: push\njobs:\n  call:\n" + guard + "    uses: ./.github/workflows/cov.yml\n"
    )
    callee = PULL_REQUEST_LANE.replace(
        "on:\n  push:\n    branches: [main]\n  pull_request:\n",
        "on:\n  workflow_call:\n",
    ).replace("        if: github.event_name == 'pull_request'\n", "")
    documents = _documents(tree(extra={"caller.yml": caller, "cov.yml": callee}))
    found = second_writer_violations(documents, "coverage-main.yml", REPOSITORY)
    assert bool(found) == expected, found


@pytest.mark.parametrize(
    ("guard", "expected"),
    [
        ("        if: github.event_name == 'pull_request'\n", False),
        ("", True),
    ],
)
def test_a_guarded_step_running_a_local_action_is_not_a_push_writer(
    guard: str, *, expected: bool
) -> None:
    """A step guarded to pull requests never runs its local action on a push."""
    caller = (
        "on: push\njobs:\n  lane:\n    runs-on: x\n    steps:\n"
        "      - uses: ./.github/actions/cov\n" + guard
    )
    action = (
        "runs:\n  using: composite\n  steps:\n"
        "    - uses: leynos/shared-actions/.github/actions/generate-coverage@abc\n"
        "      with:\n        with-ratchet: 'true'\n"
    )
    texts = tree(extra={"caller.yml": caller, ".github/actions/cov": action})
    found = second_writer_violations(parse_tree(texts), "coverage-main.yml", REPOSITORY)
    assert bool(found) == expected, found


def test_a_differently_cased_action_is_still_a_second_writer() -> None:
    """GitHub resolves the owner without case, so the rule must too."""
    texts = mutate("ci.yml", "        if: github.event_name == 'pull_request'\n", "")
    texts["ci.yml"] = texts["ci.yml"].replace(
        "leynos/shared-actions", "Leynos/Shared-Actions"
    )
    found = second_writer_violations(_documents(texts), "coverage-main.yml", REPOSITORY)
    assert found, found


def test_a_push_lane_cannot_write_a_baseline_through_a_callee() -> None:
    """A push workflow's local callee runs on the push, so its coverage counts."""
    caller = "on: push\njobs:\n  call:\n    uses: ./.github/workflows/cov.yml\n"
    callee = PULL_REQUEST_LANE.replace(
        "on:\n  push:\n    branches: [main]\n  pull_request:\n",
        "on:\n  workflow_call:\n",
    ).replace("        if: github.event_name == 'pull_request'\n", "")
    documents = _documents(tree(extra={"caller.yml": caller, "cov.yml": callee}))
    found = second_writer_violations(documents, "coverage-main.yml", REPOSITORY)
    assert (
        "cov.yml: generate-coverage can run on a push; guard it to pull requests"
        in found
    ), found


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        (
            "ci.yml",
            "          output-path: coverage.xml\n",
            "          output-path: other.xml\n",
        ),
        ("coverage-main.yml", "          with-ratchet: 'true'\n", ""),
        ("ci.yml", "generate-coverage@" + "a" * 40, "generate-coverage@" + "b" * 40),
    ],
)
def test_the_publisher_measures_what_each_lane_measures(
    name: str, old: str, new: str
) -> None:
    """A selection or pin differing from the publisher's is refused."""
    documents = _documents(mutate(name, old, new))
    closure = {"ci.yml": documents["ci.yml"]}
    found = publisher_lane_violations(documents["coverage-main.yml"], closure)
    assert found, found


#: The interpreter pin both coverage steps carry in the fixture tree.
PIN_ENV = "        env:\n          UV_PYTHON: '3.13'\n"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (PIN_ENV, ""),
        ("UV_PYTHON: '3.13'", "UV_PYTHON: '3.14'"),
        ("UV_PYTHON: '3.13'", "UV_PYTHON: '3.13'\n          EXTRA: 'x'"),
    ],
)
def test_a_lane_measures_under_the_publishers_interpreter(old: str, new: str) -> None:
    """A lane measuring under another Python counts lines differently."""
    documents = _documents(mutate("ci.yml", old, new))
    closure = {"ci.yml": documents["ci.yml"]}
    found = publisher_lane_violations(documents["coverage-main.yml"], closure)
    assert found == ["ci.yml: coverage selection differs from the publisher's"], found


def test_the_configured_interpreter_is_accepted() -> None:
    """The fixture publisher pins exactly the configured version."""
    documents = _documents(tree())
    found = interpreter_violations(documents["coverage-main.yml"], "3.13")
    assert found == [], found


@pytest.mark.parametrize(
    "value",
    ["null", "'3'", "'>=3.12'", "3.13", "''", "'3.14'"],
)
def test_an_unpinned_or_other_interpreter_is_refused(value: str) -> None:
    """Only the configured explicit version stops the venv floating."""
    texts = mutate("coverage-main.yml", "UV_PYTHON: '3.13'", f"UV_PYTHON: {value}")
    documents = _documents(texts)
    found = interpreter_violations(documents["coverage-main.yml"], "3.13")
    assert len(found) == 1, found
    assert "UV_PYTHON" in found[0], found


def test_an_unpinned_publisher_is_refused() -> None:
    """With no `env`, the venv takes the newest Python on the runner."""
    documents = _documents(mutate("coverage-main.yml", PIN_ENV, ""))
    found = interpreter_violations(documents["coverage-main.yml"], "3.13")
    assert len(found) == 1, found


@pytest.mark.parametrize("interpreter", ["3", ">=3.12", "", "3.x"])
def test_an_unbounded_configured_interpreter_is_refused(interpreter: str) -> None:
    """A configured request uv would widen cannot be the pin."""
    documents = _documents(tree())
    found = interpreter_violations(documents["coverage-main.yml"], interpreter)
    assert found and "explicit version" in found[0], found
