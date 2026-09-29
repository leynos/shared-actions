"""Refusal cases for the pull-request surface: nothing there reaches CodeScene.

Each case changes one thing in the compliant fixture tree and names the
rule that must refuse it, so deleting that rule's clause fails the case.
"""

from __future__ import annotations

import textwrap

import pytest
from contract_fixtures import (
    REPOSITORY,
    mutate,
    parse_tree,
    tree,
    violations,
)
from cv005_contracts.closure import local_action, local_callee
from cv005_contracts.loading import (
    WorkflowReadingError,
)
from cv005_contracts.reach import pull_request_violations

#: A reusable workflow declaring only `workflow_call`, curling the
#: CodeScene project API with the credential it inherits.
CALLEE = textwrap.dedent("""\
    on:
      workflow_call:
    jobs:
      probe:
        runs-on: ubuntu-latest
        steps:
          - run: 'curl -H "Authorization: $TOKEN" https://api.codescene.io/v2/projects'
            env:
              TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}
    """)

#: The credential as a workflow expression reads it.
REFERENCE = "${{ secrets.CS_ACCESS_TOKEN }}"

#: A step that curls the CodeScene host, for the trigger-form cases.
CURL_JOB = "jobs:\n  a:\n    runs-on: x\n    steps:\n      - run: curl codescene.io\n"

#: A pull-request job calling a local workflow and forwarding everything.
CALLER = textwrap.dedent("""\
    on: [pull_request]
    jobs:
      call:
        uses: {spelling}.github/workflows/{callee}
        secrets: inherit
    """)


def _findings(texts: dict[str, str]) -> list[str]:
    """Return the pull-request surface findings over a tree."""
    return pull_request_violations(parse_tree(texts), REPOSITORY)


def test_the_compliant_tree_passes_every_rule() -> None:
    """The fixture the refusal cases start from is itself compliant."""
    found = violations(tree())
    assert not found, found


@pytest.mark.parametrize("spelling", ["./", "$/"])
def test_a_called_workflow_is_inside_the_closure(spelling: str) -> None:
    """A `workflow_call` callee reached through either local spelling is read."""
    texts = tree(
        extra={
            "probe.yml": CALLER.format(spelling=spelling, callee="callee.yml"),
            "callee.yml": CALLEE,
        }
    )
    findings = _findings(texts)
    assert any(item.startswith("callee.yml: names") for item in findings), findings
    assert "probe.yml: job call uses secrets: inherit" in findings, findings


def test_the_closure_follows_a_chain_of_calls() -> None:
    """A callee two calls deep is reached, not only a direct one."""
    middle = textwrap.dedent("""\
        on:
          workflow_call:
        jobs:
          call:
            uses: ./.github/workflows/callee.yml
        """)
    texts = tree(
        extra={
            "probe.yml": CALLER.format(spelling="./", callee="middle.yml").replace(
                "    secrets: inherit\n", ""
            ),
            "middle.yml": middle,
            "callee.yml": CALLEE,
        }
    )
    findings = _findings(texts)
    assert any(item.startswith("callee.yml: names") for item in findings), findings


@pytest.mark.parametrize(
    ("where", "text"),
    [
        ("run body", "      - run: echo ${{ secrets.CS_ACCESS_TOKEN }}\n"),
        (
            "action input",
            f"      - uses: x/y@v1\n        with:\n          t: {REFERENCE}\n",
        ),
        (
            "renamed env",
            f"      - run: 'true'\n        env:\n          OTHER: {REFERENCE}\n",
        ),
        ("shell curl", "      - run: curl https://API.CodeScene.IO/v2\n"),
        ("every secret", "      - run: echo '${{ toJSON(secrets) }}'\n"),
        (
            "assembled name",
            "      - run: echo ${{ secrets[format('CS_{0}', 'ACCESS_TOKEN')] }}\n",
        ),
        (
            "literal closer",
            "      - run: echo ${{ format('}}', toJSON(secrets)) }}\n",
        ),
        ("unterminated", "      - run: echo ${{ toJSON(secrets)\n"),
        ("client", "      - run: cs-coverage check coverage.xml\n"),
        (
            "uploader",
            "      - uses: o/r/.github/actions/upload-codescene-coverage@v1\n",
        ),
    ],
)
def test_a_pull_request_step_cannot_reach_codescene(where: str, text: str) -> None:
    """The credential, host, client or uploader in any step is refused."""
    texts = mutate("ci.yml", "      - uses: actions/checkout@v4\n", text)
    assert _findings(texts), where


@pytest.mark.parametrize(
    "run",
    ["echo ${{ secrets.OTHER }}", "echo ${{ secrets['OTHER'] }}", "echo secrets"],
)
def test_a_named_secret_read_is_not_an_unnamed_one(run: str) -> None:
    """A literal name, or the word outside an expression, is not refused."""
    texts = mutate(
        "ci.yml", "      - uses: actions/checkout@v4\n", f"      - run: {run}\n"
    )
    assert not _findings(texts), run


def test_named_secret_forwarding_is_refused() -> None:
    """A reusable call forwarding the credential by name is refused."""
    caller = textwrap.dedent("""\
        on: pull_request
        jobs:
          call:
            uses: leynos/shared-actions/.github/workflows/x.yml@main
            secrets:
              token: ${{ secrets.CS_ACCESS_TOKEN }}
        """)
    findings = _findings(tree(extra={"probe.yml": caller}))
    assert findings, findings


def test_workflow_defaults_and_secret_declarations_are_read() -> None:
    """`defaults.run.shell` and a `workflow_call` secret key are both scanned."""
    shell = "defaults:\n  run:\n    shell: bash -c 'curl codescene.io; bash {0}'\njobs:"
    declared = (
        CALLEE.replace(
            "  workflow_call:\n",
            "  workflow_call:\n    secrets:\n      CS_ACCESS_TOKEN:\n",
        )
        .replace("https://api.codescene.io/v2/projects", "example.org")
        .replace("${{ secrets.CS_ACCESS_TOKEN }}", "x")
    )
    texts = mutate("ci.yml", "jobs:", shell) | {
        "probe.yml": CALLER.format(spelling="./", callee="callee.yml"),
        "callee.yml": declared,
    }
    findings = _findings(texts)
    assert any(item.startswith("ci.yml: names") for item in findings), findings
    assert any(
        item.startswith("callee.yml: names 'CS_ACCESS_TOKEN'") for item in findings
    ), findings


@pytest.mark.parametrize(
    "form",
    [
        "on: pull_request\n",
        "on: [push, pull_request]\n",
        "'on':\n  pull_request_target:\n",
    ],
)
def test_every_trigger_form_seeds_the_closure(form: str) -> None:
    """Scalar, sequence and mapping trigger forms all start the closure."""
    probe = form + CURL_JOB
    findings = _findings(tree(extra={"probe.yml": probe}))
    assert any(item.startswith("probe.yml") for item in findings), findings


@pytest.mark.parametrize(
    "trigger",
    [
        "issue_comment",
        "merge_group",
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "pull_request_target",
    ],
)
def test_every_pull_request_event_seeds_the_closure(trigger: str) -> None:
    """Each event that runs for a pull request starts the closure."""
    probe = f"on: {trigger}\n" + CURL_JOB
    findings = _findings(tree(extra={"probe.yml": probe}))
    assert any(item.startswith("probe.yml") for item in findings), findings


@pytest.mark.parametrize(
    "form",
    [
        "on: push\n",
        "on:\n  push:\n    branches: ['**']\n",
        "on:\n  push:\n    branches-ignore: [main]\n",
        "on:\n  push:\n    branches: [main, release]\n",
        "on:\n  push:\n    paths: ['src/**']\n",
    ],
)
def test_a_push_beyond_main_is_inside_the_closure(form: str) -> None:
    """A push that can run for a pull request's branch seeds the closure."""
    findings = _findings(tree(extra={"probe.yml": form + CURL_JOB}))
    assert any(item.startswith("probe.yml") for item in findings), findings


@pytest.mark.parametrize(
    "form",
    [
        "on:\n  push:\n    branches: [main]\n",
        "on:\n  push:\n    tags: ['v*']\n",
    ],
)
def test_a_push_to_main_or_tags_is_outside_the_closure(form: str) -> None:
    """A push confined to the trunk or to tags does not seed the closure."""
    findings = _findings(tree(extra={"probe.yml": form + CURL_JOB}))
    assert not any(item.startswith("probe.yml") for item in findings), findings


def test_a_workflow_run_chain_is_inside_the_closure() -> None:
    """A workflow chained onto another's run is treated as reachable."""
    probe = "on:\n  workflow_run:\n    workflows: [CI]\n" + CURL_JOB
    findings = _findings(tree(extra={"probe.yml": probe}))
    assert any(item.startswith("probe.yml") for item in findings), findings


@pytest.mark.parametrize(
    "reference",
    [
        "leynos/example/.github/workflows/callee.yml@main",
        "LEYNOS/Example/.github/workflows/callee.yml@v1",
        "$/.github/workflows/callee.yml@main",
    ],
)
def test_a_call_this_checkout_cannot_read_is_refused(reference: str) -> None:
    """A self-call at a ref runs a file this checkout does not hold."""
    with pytest.raises(WorkflowReadingError):
        local_callee(reference, REPOSITORY)


def test_a_call_to_a_missing_local_workflow_is_refused() -> None:
    """A closure naming a file the tree lacks is the reader failing."""
    texts = tree(extra={"probe.yml": CALLER.format(spelling="./", callee="absent.yml")})
    with pytest.raises(WorkflowReadingError, match="does not exist"):
        _findings(texts)


#: A pull-request job running a local action, which runs another.
ACTION_CALLER = (
    "on: [pull_request]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
    "      - uses: {spelling}.github/actions/outer\n"
)
OUTER_ACTION = (
    "runs:\n  using: composite\n  steps:\n    - uses: ./.github/actions/inner\n"
)
INNER_ACTION = "runs:\n  using: composite\n  steps:\n    - run: cs-coverage upload\n"


@pytest.mark.parametrize("spelling", ["./", "$/"])
def test_a_local_action_is_inside_the_closure(spelling: str) -> None:
    """A local action runs in its caller's job, and so does one it runs."""
    texts = tree(
        extra={
            "probe.yml": ACTION_CALLER.format(spelling=spelling),
            ".github/actions/outer": OUTER_ACTION,
            ".github/actions/inner": INNER_ACTION,
        }
    )
    findings = _findings(texts)
    assert any(item.startswith(".github/actions/inner") for item in findings), findings


def test_a_local_action_the_tree_lacks_is_refused() -> None:
    """A closure naming an action the tree lacks is the reader failing."""
    texts = tree(extra={"probe.yml": ACTION_CALLER.format(spelling="./")})
    with pytest.raises(WorkflowReadingError, match="does not exist"):
        _findings(texts)


@pytest.mark.parametrize(
    "reference",
    [
        "./.github/actions/outer@main",
        "leynos/example/.github/actions/outer@main",
        "LEYNOS/Example/.github/actions/outer@v1",
        "leynos/example@main",
    ],
)
def test_a_local_action_at_a_ref_is_refused(reference: str) -> None:
    """A local action at a ref runs a version this checkout does not hold."""
    with pytest.raises(WorkflowReadingError, match="ref"):
        local_action(reference, REPOSITORY)


def test_another_repository_is_not_followed() -> None:
    """A cross-repository call is out of this tree and is not a local callee."""
    callee = local_callee("leynos/other/.github/workflows/x.yml@main", REPOSITORY)
    assert callee is None, callee


@pytest.mark.parametrize(
    "event",
    ["issues", "repository_dispatch", "check_suite", "deployment", "discussion"],
)
def test_an_event_outside_the_allowlist_seeds_the_closure(event: str) -> None:
    """Seeding fails closed: an event nobody vetted counts as pull-request surface."""
    probe = f"on: [{event}]\n" + CURL_JOB
    findings = _findings(tree(extra={"probe.yml": probe}))
    assert any(item.startswith("probe.yml") for item in findings), findings


@pytest.mark.parametrize(
    "event", ["schedule", "release", "workflow_dispatch", "workflow_call"]
)
def test_an_allowlisted_event_stays_off_the_surface(event: str) -> None:
    """The five vetted events cannot run a workflow for a pull request."""
    probe = f"on: [{event}]\n" + CURL_JOB
    findings = _findings(tree(extra={"probe.yml": probe}))
    assert not any(item.startswith("probe.yml") for item in findings), findings
