"""A compliant workflow tree for the rule tests, and a way to break it.

Every refusal case starts from this tree and changes one thing, and the
tree itself must pass every rule, so each case proves the rule refuses
the change rather than something the fixture already got wrong.
"""

from __future__ import annotations

import textwrap
import typing as typ

from cv005_contracts.actions import load_action
from cv005_contracts.concurrency import concurrency_violations
from cv005_contracts.credential import check_step_violations, token_scope_violations
from cv005_contracts.lanes import (
    pull_request_lane_violations,
    second_writer_violations,
)
from cv005_contracts.loading import Document, load_workflow
from cv005_contracts.parity import publisher_lane_violations
from cv005_contracts.permissions import permissions_violations
from cv005_contracts.publisher import find_publisher
from cv005_contracts.publisher_rules import (
    condition_violations,
    retired_checksum_violations,
    trigger_violations,
    upload_step_violations,
)
from cv005_contracts.reach import pull_request_closure, pull_request_violations
from cv005_contracts.wiring import wiring_violations

REPOSITORY: typ.Final[str] = "leynos/example"
PIN: typ.Final[str] = "a" * 40
SHARED: typ.Final[str] = "leynos/shared-actions/.github/actions"

PULL_REQUEST_LANE: typ.Final[str] = textwrap.dedent(f"""\
    name: CI
    on:
      push:
        branches: [main]
      pull_request:
    jobs:
      build-test:
        runs-on: ubuntu-latest
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
          - name: Test and Measure Coverage
            if: github.event_name == 'pull_request'
            env:
              UV_PYTHON: '3.13'
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              artefact-name-suffix: example
              with-ratchet: 'true'
              publish-artefact: 'false'
    """)

PUBLISHER: typ.Final[str] = textwrap.dedent(f"""\
    name: Coverage (main)
    on:
      push:
        branches: [main]
      workflow_dispatch:
    permissions: {{}}
    concurrency:
      group: coverage-main-${{{{ github.ref }}}}
      cancel-in-progress: false
    jobs:
      coverage-upload:
        runs-on: ubuntu-latest
        environment: codescene
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
            with:
              persist-credentials: false
          - name: Generate coverage
            env:
              UV_PYTHON: '3.13'
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              artefact-name-suffix: example
              with-ratchet: 'true'
          - name: Check for the CodeScene token
            id: codescene-token
            run: echo "available=${{{{ secrets.CS_ACCESS_TOKEN != '' }}}}" >> "$GITHUB_OUTPUT"
          - name: Upload coverage data to CodeScene
            if: steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main'
            uses: {SHARED}/upload-codescene-coverage@{PIN}
            with:
              path: coverage.xml
              mode: upload
              access-token: ${{{{ secrets.CS_ACCESS_TOKEN }}}}
    """)  # noqa: E501 - two lines must match the real workflow's.

TREE: typ.Final[dict[str, str]] = {
    "ci.yml": PULL_REQUEST_LANE,
    "coverage-main.yml": PUBLISHER,
}


def tree(*, extra: dict[str, str] | None = None, **replaced: str) -> dict[str, str]:
    """Return the compliant tree's texts with files replaced or added.

    A keyword names a file by its stem (`ci`, `coverage_main`).

    Parameters
    ----------
    extra : dict[str, str] | None, optional
        Additional files to add, keyed by their full file name.
    **replaced : str
        Replacement texts for compliant files, keyed by file stem.

    Returns
    -------
    dict[str, str]
        The compliant tree's file names mapped to their texts.

    """
    texts = dict(TREE)
    for stem, text in replaced.items():
        texts[f"{stem.replace('_', '-')}.yml"] = text
    return texts | (extra or {})


def mutate(name: str, old: str, new: str) -> dict[str, str]:
    """Return the compliant tree with one exact substitution in one file.

    Parameters
    ----------
    name : str
        The file name to mutate, matching a key in the compliant tree.
    old : str
        The text to replace.
    new : str
        The replacement text.

    Returns
    -------
    dict[str, str]
        The compliant tree's file names mapped to their texts, with
        `name`'s text mutated.

    Raises
    ------
    ValueError
        If the text to replace is absent or occurs more than once. A
        mutation that changes nothing passes for a reason that proves
        nothing, and one that changes two places can pass on the second.

    """
    text = TREE[name]
    count = text.count(old)
    if count != 1:
        message = (
            f"{old!r} occurs {count} times in {name}; a mutation must change "
            "exactly one thing"
        )
        raise ValueError(message)
    return tree() | {name: text.replace(old, new, 1)}


def parse_tree(texts: dict[str, str]) -> dict[str, Document]:
    """Parse a tree of texts as `read_workflows` and `read_actions` would.

    A name holding a `/` is a local action's path, such as
    `.github/actions/build`, as `read_actions` keys it; any other name is a
    workflow file.

    Parameters
    ----------
    texts : dict[str, str]
        Workflow file names and action paths mapped to their YAML texts.

    Returns
    -------
    dict[str, Document]
        Each text parsed, by the same name.

    """
    return {
        name: (load_action if "/" in name else load_workflow)(text)
        for name, text in texts.items()
    }


def violations(texts: dict[str, str]) -> list[str]:
    """Return every CV-005 finding over a tree of workflow texts.

    A workflow that cannot be read, or a tree whose shape defeats a
    reading, such as one with a second publisher, raises
    `WorkflowReadingError` from the reading it defeats.

    Parameters
    ----------
    texts : dict[str, str]
        Workflow file names mapped to their YAML texts.

    Returns
    -------
    list[str]
        Every CV-005 violation found across the tree.

    """
    documents = parse_tree(texts)
    closure = pull_request_closure(documents, REPOSITORY)
    name, publisher = find_publisher(documents)
    return [
        *pull_request_violations(documents, REPOSITORY),
        *trigger_violations(publisher),
        *concurrency_violations(publisher),
        *check_step_violations(publisher),
        *upload_step_violations(publisher),
        *token_scope_violations(publisher),
        *permissions_violations(publisher),
        *wiring_violations(publisher),
        *condition_violations(publisher),
        *retired_checksum_violations(documents),
        *pull_request_lane_violations(closure),
        *second_writer_violations(documents, name, REPOSITORY),
        *publisher_lane_violations(publisher, closure),
    ]
