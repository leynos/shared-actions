"""Cases for finding the token check step through the upload guard.

The estate names the check step more than one way, so the step is the one
whose `available` output the upload guard reads. These cases hold both
directions: any id the guard reads is followed, and only that one.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PUBLISHER, mutate
from cv005_contracts.credential import check_step_violations, token_scope_violations
from cv005_contracts.loading import load_workflow
from cv005_contracts.publisher_rules import upload_step_violations

CHECK_ID_LINE = "        id: codescene-token\n"
AVAILABLE = "steps.codescene-token.outputs.available == 'true'"
CHECK_RUN = (
    "        run: echo \"available=${{ secrets.CS_ACCESS_TOKEN != '' }}\""
    ' >> "$GITHUB_OUTPUT"\n'
)


def _renamed(step_id: str, guard_id: str) -> str:
    """Return the publisher with the check step's id and the guard's id set."""
    text = PUBLISHER.replace(CHECK_ID_LINE, f"        id: {step_id}\n")
    return text.replace(AVAILABLE, AVAILABLE.replace("codescene-token", guard_id))


def test_a_check_step_the_guard_names_is_followed_under_any_id() -> None:
    """The chutoro spelling, `codescene-credential`, satisfies every token rule."""
    publisher = load_workflow(_renamed("codescene-credential", "codescene-credential"))
    found = (
        check_step_violations(publisher)
        + token_scope_violations(publisher)
        + upload_step_violations(publisher)
    )
    assert not found, found


def test_a_renamed_guard_without_its_step_is_refused() -> None:
    """A guard reading a step that does not exist would skip for ever."""
    publisher = load_workflow(_renamed("codescene-token", "codescene-credential"))
    found = check_step_violations(publisher)
    assert found == ["the upload job must hold one `codescene-credential` step"], found


def test_the_guard_may_read_only_one_availability_output() -> None:
    """A second availability term leaves which step is the check ambiguous."""
    second = "steps.other.outputs.available == 'true'"
    texts = mutate("coverage-main.yml", AVAILABLE, f"{AVAILABLE} && {second}")
    found = upload_step_violations(load_workflow(texts["coverage-main.yml"]))
    assert any("more than one step" in problem for problem in found), found


def test_only_the_step_the_guard_reads_is_exempt_from_the_sweep() -> None:
    """The check command under an id the guard does not read is refused."""
    decoy = "      - id: codescene-credential\n" + CHECK_RUN
    text = PUBLISHER.replace(
        CHECK_ID_LINE + CHECK_RUN, CHECK_ID_LINE + CHECK_RUN + decoy
    )
    assert text != PUBLISHER
    found = token_scope_violations(load_workflow(text))
    assert found, found


@pytest.mark.parametrize(
    "run_defaults",
    [
        "      run:\n        shell: bash\n",
        "      run:\n        shell: sh\n        working-directory: src\n",
    ],
)
def test_plain_run_defaults_leave_the_check_as_written(run_defaults: str) -> None:
    """The whitaker default, `shell: bash`, runs the one `echo` unchanged."""
    anchor = "    runs-on: ubuntu-latest\n    environment: codescene\n"
    text = PUBLISHER.replace(anchor, anchor + "    defaults:\n" + run_defaults)
    assert text != PUBLISHER
    found = check_step_violations(load_workflow(text))
    assert not found, found


@pytest.mark.parametrize(
    "run_defaults",
    [
        "      run:\n        shell: pwsh\n",
        "      run:\n        shell: bash\n        other: x\n",
        "      run: bash\n",
    ],
)
def test_other_run_defaults_are_refused(run_defaults: str) -> None:
    """A shell the `echo` does not run under as written is refused."""
    anchor = "    runs-on: ubuntu-latest\n    environment: codescene\n"
    text = PUBLISHER.replace(anchor, anchor + "    defaults:\n" + run_defaults)
    found = check_step_violations(load_workflow(text))
    assert found, found
