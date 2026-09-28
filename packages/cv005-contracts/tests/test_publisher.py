"""Refusal cases for the publisher.

Each case changes one thing in the compliant fixture tree and asserts on
the one rule that must refuse it, so deleting that rule's clause fails
the case.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PUBLISHER, mutate, parse_tree, tree
from cv005_contracts.credential import (
    check_step_violations,
    token_scope_violations,
)
from cv005_contracts.loading import (
    Document,
    WorkflowReadingError,
    load_workflow,
)
from cv005_contracts.publisher import find_publisher
from cv005_contracts.publisher_rules import (
    concurrency_violations,
    permissions_violations,
    retired_checksum_violations,
    trigger_violations,
    upload_step_violations,
)

AVAILABLE = "steps.codescene-token.outputs.available == 'true'"
MAIN = "github.ref == 'refs/heads/main'"
GUARD = f"if: {AVAILABLE} && {MAIN}"
CHECK_RUN = (
    "        run: echo \"available=${{ secrets.CS_ACCESS_TOKEN != '' }}\""
    ' >> "$GITHUB_OUTPUT"\n'
)
CHECK_STEP = (
    "      - name: Check for the CodeScene token\n"
    "        id: codescene-token\n" + CHECK_RUN
)
UPLOAD_NAME = "      - name: Upload coverage data to CodeScene\n"
TOKEN_INPUT = "          access-token: ${{ secrets.CS_ACCESS_TOKEN }}\n"  # noqa: S105 - an expression, not a secret.


def _publisher(texts: dict[str, str]) -> Document:
    """Return the parsed publisher of a tree."""
    return load_workflow(texts["coverage-main.yml"])


def _documents(texts: dict[str, str]) -> dict[str, Document]:
    """Parse a tree of texts."""
    return {name: load_workflow(text) for name, text in texts.items()}


@pytest.mark.parametrize(
    "guard",
    [
        # Every required term stays whole; only the `||` refusal catches it.
        f"{GUARD} && github.actor != 'x' || github.event_name == 'workflow_dispatch'",
        f"if: {AVAILABLE}",
        f"if: {MAIN}",
        f"if: ${{{{ !({AVAILABLE} && {MAIN}) }}}}",
        f"if: ({AVAILABLE} && {MAIN}",
        f"if: {AVAILABLE} && github.ref != 'refs/heads/main'",
        f"if: env.CS_ACCESS_TOKEN != '' && {MAIN}",
        # An embedded expression makes the whole condition a template, and
        # the non-empty string it renders is always true.
        f"{GUARD} && ${{{{ true }}}}",
        f"if: ${{{{ {AVAILABLE} }}}} && ${{{{ {MAIN} }}}}",
    ],
)
def test_the_upload_guard_needs_both_terms_and_no_disjunction(guard: str) -> None:
    """The ref and availability guard must hold as whole terms of a conjunction."""
    texts = mutate("coverage-main.yml", GUARD, guard)
    found = upload_step_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize(
    "guard",
    [
        f"{GUARD} && github.actor != 'x'",
        f"{GUARD} && (github.actor != 'x' || github.run_attempt == '1')",
        f"if: ${{{{ {MAIN} && {AVAILABLE} }}}}",
        f"{GUARD} && 'a||b' != ''",
    ],
)
def test_a_narrower_upload_guard_is_accepted(guard: str) -> None:
    """Extra terms, a wrapper and a quoted `||` do not trip the guard rule."""
    texts = mutate("coverage-main.yml", GUARD, guard)
    found = upload_step_violations(_publisher(texts))
    assert not found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (TOKEN_INPUT, ""),
        (TOKEN_INPUT, "          access-token: ${{ env.CS_ACCESS_TOKEN }}\n"),
        ("          mode: upload\n", "          mode: check\n"),
        ("upload-codescene-coverage@" + "a" * 40, "upload-codescene-coverage@main"),
    ],
)
def test_the_upload_step_passes_the_token_directly(old: str, new: str) -> None:
    """A missing or indirect input, check mode or a branch pin is refused."""
    texts = mutate("coverage-main.yml", old, new)
    found = upload_step_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (CHECK_STEP, ""),
        (CHECK_RUN, '        run: echo "available=true" >> "$GITHUB_OUTPUT"\n'),
        (
            CHECK_RUN,
            (
                "        run: false && echo "
                "\"available=${{ secrets.CS_ACCESS_TOKEN != '' }}\""
                ' >> "$GITHUB_OUTPUT"\n'
            ),
        ),
        ("        id: codescene-token\n", "        id: token\n"),
        (CHECK_RUN, CHECK_RUN + "        if: github.actor == 'x'\n"),
        (CHECK_RUN, CHECK_RUN + "        shell: bash -c 'exit 0; {0}'\n"),
        (CHECK_RUN, CHECK_RUN + "        continue-on-error: true\n"),
    ],
)
def test_the_check_step_runs_its_one_command(old: str, new: str) -> None:
    """A deleted, rewritten, renamed or conditional check step is refused."""
    texts = mutate("coverage-main.yml", old, new)
    found = check_step_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("jobs:\n", "defaults:\n  run:\n    shell: bash -c 'exit 0; {0}'\njobs:\n"),
        (
            "    runs-on: ubuntu-latest\n",
            (
                "    runs-on: ubuntu-latest\n    defaults:\n      run:\n"
                "        shell: bash -c 'exit 0; {0}'\n"
            ),
        ),
    ],
)
def test_run_defaults_cannot_reshape_the_check(old: str, new: str) -> None:
    """A workflow or upload-job default shell reaches the check step too."""
    texts = mutate("coverage-main.yml", old, new)
    found = check_step_violations(_publisher(texts))
    assert found, found


def test_the_check_step_must_precede_the_upload() -> None:
    """A check step after the upload leaves the guard reading nothing."""
    text = PUBLISHER.replace(CHECK_STEP, "") + CHECK_STEP
    found = check_step_violations(load_workflow(text))
    assert found == ["the check step must run before the upload step"], found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "    runs-on: ubuntu-latest\n",
            (
                "    runs-on: ubuntu-latest\n"
                "    env:\n      CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}\n"
            ),
        ),
        (
            "concurrency:\n",
            "env:\n  T: ${{ secrets.CS_ACCESS_TOKEN }}\nconcurrency:\n",
        ),
        (
            UPLOAD_NAME,
            UPLOAD_NAME
            + (
                "        env:\n"
                "          CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}\n"
            ),
        ),
        (
            "        id: codescene-token\n",
            (
                "        id: codescene-token\n"
                "        env:\n"
                "          CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}\n"
            ),
        ),
        (
            "      - uses: actions/checkout@v4\n",
            "      - run: echo ${{ secrets.CS_ACCESS_TOKEN }}\n",
        ),
    ],
)
def test_the_token_is_refused_outside_its_two_uses(old: str, new: str) -> None:
    """An `env` binding at any level, or another step, may not hold the token."""
    texts = mutate("coverage-main.yml", old, new)
    found = token_scope_violations(_publisher(texts))
    assert found, found


@pytest.mark.parametrize(
    "run",
    [
        "echo ${{ toJSON(secrets) }}",
        "echo ${{ secrets[format('CS_{0}', 'ACCESS_TOKEN')] }}",
        "echo ${{ format('}}', toJSON(secrets)) }}",
    ],
)
def test_an_unnamed_secret_read_is_refused_in_the_publisher(run: str) -> None:
    """Reading the secrets context without a literal name escapes the sweep."""
    texts = mutate(
        "coverage-main.yml",
        "      - uses: actions/checkout@v4\n",
        f"      - run: {run}\n",
    )
    found = token_scope_violations(_publisher(texts))
    assert found, found


def test_the_publisher_grants_no_workflow_scope() -> None:
    """A workflow-level scope reaches every job, so it must be empty."""
    texts = mutate("coverage-main.yml", "permissions: {}\n", "")
    found = permissions_violations(_publisher(texts))
    assert found, found


def test_the_token_sweep_excludes_only_the_upload_job_check() -> None:
    """A second job's step reusing the check id gains no exemption."""
    second = (
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - id: codescene-token\n"
        "        run: echo ${{ secrets.CS_ACCESS_TOKEN }}\n"
    )
    text = PUBLISHER.replace("jobs:\n", "jobs:\n" + second)
    found = token_scope_violations(load_workflow(text))
    assert found, found


GROUP = "group: coverage-main-${{ github.ref }}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("cancel-in-progress: false", "cancel-in-progress: true"),
        (
            "cancel-in-progress: false",
            "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}",
        ),
        ("  cancel-in-progress: false\n", ""),
        (GROUP, "group: coverage-main-${{ github.ref }}-${{ github.event_name }}"),
        (GROUP, "group: coverage-main"),
        (GROUP, "group: coverage-main-github.ref"),
        (
            f"concurrency:\n  {GROUP}\n  cancel-in-progress: false\n",
            "",
        ),
        (
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    concurrency: upload\n",
        ),
    ],
)
def test_the_publisher_holds_its_one_ref_keyed_group(old: str, new: str) -> None:
    """A cancelling, event-keyed, constant, missing or second group is refused."""
    texts = mutate("coverage-main.yml", old, new)
    found = concurrency_violations(_publisher(texts))
    assert found, found


def test_another_job_may_not_declare_concurrency() -> None:
    """Only the workflow's or the upload job's group governs the upload."""
    other = (
        "  other:\n    runs-on: ubuntu-latest\n    concurrency: other\n"
        "    steps:\n      - run: 'true'\n"
    )
    text = PUBLISHER.replace("jobs:\n", "jobs:\n" + other)
    found = concurrency_violations(load_workflow(text))
    assert found == ["job other declares its own concurrency"], found


#: The fixture's workflow-level declaration, and the same moved to the job.
WORKFLOW_GROUP = f"concurrency:\n  {GROUP}\n  cancel-in-progress: false\n"
JOB_GROUP = (
    "    runs-on: ubuntu-latest\n    concurrency:\n"
    "      group: coverage-main-${{ github.ref }}\n      cancel-in-progress: false\n"
)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (GROUP, "group: ${{ github.workflow }}-${{ github.ref }}"),
        (GROUP, "group: coverage-main-${{github.ref}}"),
        (GROUP, "group: ${{github.workflow}}-${{  github.ref  }}"),
    ],
)
def test_either_estate_spelling_of_the_group_is_accepted(old: str, new: str) -> None:
    """The stem or `github.workflow`, with any spacing inside the braces."""
    texts = mutate("coverage-main.yml", old, new)
    found = concurrency_violations(_publisher(texts))
    assert found == [], found


def test_the_group_may_sit_on_the_upload_job() -> None:
    """The upload job's own group governs the upload as well as the workflow's."""
    text = PUBLISHER.replace(WORKFLOW_GROUP, "").replace(
        "    runs-on: ubuntu-latest\n", JOB_GROUP
    )
    found = concurrency_violations(load_workflow(text))
    assert found == [], found


def test_the_group_is_declared_at_one_scope_only() -> None:
    """GitHub deadlocks on one group at both scopes and cancels the job."""
    text = PUBLISHER.replace("    runs-on: ubuntu-latest\n", JOB_GROUP)
    found = concurrency_violations(load_workflow(text))
    assert any("one scope" in item for item in found), found


def test_an_unrelated_job_group_does_not_govern() -> None:
    """A group on another job leaves the upload ungoverned."""
    other = (
        "  other:\n    runs-on: ubuntu-latest\n    concurrency:\n"
        "      group: coverage-main-${{ github.ref }}\n"
        "      cancel-in-progress: false\n"
        "    steps:\n      - run: 'true'\n"
    )
    text = PUBLISHER.replace(WORKFLOW_GROUP, "").replace("jobs:\n", "jobs:\n" + other)
    found = concurrency_violations(load_workflow(text))
    assert found, found


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("    branches: [main]\n", "    branches: ['**']\n"),
        ("    branches: [main]\n", "    tags: ['v*']\n"),
        (
            "  workflow_dispatch:\n",
            "  workflow_dispatch:\n  schedule:\n    - cron: '0 0 * * *'\n",
        ),
    ],
)
def test_the_publisher_runs_only_on_a_push_to_main(old: str, new: str) -> None:
    """Any branch, a tag push or another trigger is refused."""
    texts = mutate("coverage-main.yml", old, new)
    found = trigger_violations(_publisher(texts))
    assert found, found


def test_a_second_uploader_is_refused() -> None:
    """Two workflows contacting CodeScene cannot both be the publisher."""
    texts = tree(extra={"second.yml": PUBLISHER})
    with pytest.raises(WorkflowReadingError, match="exactly one workflow"):
        find_publisher(_documents(texts))


def test_a_local_action_is_not_a_second_publisher() -> None:
    """Only workflows are candidates; an action is judged where it runs."""
    action = (
        "runs:\n  using: composite\n  steps:\n"
        "    - uses: leynos/shared-actions/.github/actions/"
        "upload-codescene-coverage@abc\n"
    )
    texts = tree(extra={".github/actions/upload": action})
    name, _ = find_publisher(parse_tree(texts))
    assert name == "coverage-main.yml", name


@pytest.mark.parametrize(
    "addition",
    [
        "          installer-checksum: ${{ vars.CODESCENE_CLI_SHA256 }}\n",
        "          archive-checksum: ${{ vars.CODESCENE_CLI_SHA256 }}\n",
    ],
)
def test_the_retired_checksum_is_refused(addition: str) -> None:
    """The installer checksum and its variable are gone for good."""
    texts = mutate(
        "coverage-main.yml",
        "          mode: upload\n",
        "          mode: upload\n" + addition,
    )
    found = retired_checksum_violations(_documents(texts))
    assert found, found


def test_the_checksum_refresher_is_refused() -> None:
    """The workflow that refreshed the retired checksum must not return."""
    refresher = "on: workflow_dispatch\njobs:\n  a:\n    runs-on: x\n    steps: []\n"
    texts = tree(extra={"get-codescene-sha.yml": refresher})
    found = retired_checksum_violations(_documents(texts))
    assert found, found
