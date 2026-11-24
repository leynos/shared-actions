"""Behaviour of the reusable delayed PR comment workflow's shell steps.

The two ``run:`` bodies are read from the workflow and executed under bash
with stub ``sleep`` and ``gh`` commands first on ``PATH``. The stubs record
their arguments, so each case proves what ran, not what the file says.

Run via ``make test``.
"""

from __future__ import annotations

import os
import re
import shutil
import typing as typ
from pathlib import Path

import pytest
import yaml
from plumbum import local

from ._doc_examples import load_tested_examples
from .workflow_yaml import load_workflow

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github/workflows/delayed-pr-comment.yml"
)
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

#: A stub that records its arguments, one per line, to ``<name>.calls``.
_STUB: typ.Final[str] = (
    '#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" >> "$STUB_DIR/$(basename "$0").calls"\n'
)


class StepResult(typ.NamedTuple):
    """Exit status and output of one executed step."""

    returncode: int
    stdout: str
    stderr: str


def _step_script(step_name: str) -> str:
    """Return the ``run:`` body of the named step."""
    document = typ.cast("dict[str, typ.Any]", load_workflow(WORKFLOW))
    steps = document["jobs"]["delay_and_comment"]["steps"]
    return next(step["run"] for step in steps if step["name"] == step_name)


def _run(
    tmp_path: Path,
    step_name: str,
    env: dict[str, str],
    gh_stub: str = _STUB,
) -> StepResult:
    """Run one step's script with stubbed ``sleep``, ``gh`` and ``id``."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("sleep", "gh", "id"):
        stub = stubs / name
        stub.write_text(gh_stub if name == "gh" else _STUB, encoding="utf-8")
        stub.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(_step_script(step_name), encoding="utf-8")
    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "GITHUB_REPOSITORY": "octo/repo",
        **env,
    }
    with local.cwd(tmp_path):
        code, stdout, stderr = (
            local[typ.cast("str", BASH)]
            .with_env(**environment)
            .run([str(script)], retcode=None)
        )
    return StepResult(code, stdout, stderr)


def _calls(tmp_path: Path, name: str) -> list[str]:
    """Return the recorded arguments of stub *name*, empty if never called."""
    path = tmp_path / f"{name}.calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


WAIT = "Validate inputs and wait"
COMMENT = "Comment on the pull request"


@pytest.mark.parametrize(
    ("delay", "seconds"),
    [("0", "0"), ("1", "60"), ("08", "480"), ("345", "20700"), ("0345", "20700")],
)
def test_valid_delay_sleeps_the_computed_seconds(
    tmp_path: Path, delay: str, seconds: str
) -> None:
    """A digits-only delay up to the cap sleeps once, in base 10."""
    result = _run(tmp_path, WAIT, {"PR_NUMBER": "12", "DELAY_MINUTES": delay})

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path, "sleep") == [seconds]


@pytest.mark.parametrize(
    "delay",
    [
        "$(id)",
        "1;id",
        "`id`",
        "-1",
        "+1",
        "1e3",
        "1.5",
        "346",
        "99999",
        "",
        " 1",
        "1 ",
        "1\n2",
    ],
)
def test_bad_delay_fails_before_anything_runs(tmp_path: Path, delay: str) -> None:
    """A malformed or oversized delay fails with an error and runs nothing."""
    result = _run(tmp_path, WAIT, {"PR_NUMBER": "12", "DELAY_MINUTES": delay})

    assert result.returncode != 0
    assert (
        "::error::delay_minutes must be a whole number from 0 to 345." in result.stdout
    )
    assert _calls(tmp_path, "sleep") == []
    assert _calls(tmp_path, "id") == []


@pytest.mark.parametrize(
    "pr_number", ["$(id)", "1;id", "-1", "0", "00", "", "12345", "1e3"]
)
def test_bad_pr_number_fails_before_sleeping(tmp_path: Path, pr_number: str) -> None:
    """A malformed pull-request number fails fast, before the wait."""
    result = _run(tmp_path, WAIT, {"PR_NUMBER": pr_number, "DELAY_MINUTES": "1"})

    assert result.returncode != 0
    assert "::error::pr_number must be a whole number from 1 to 9999." in result.stdout
    assert _calls(tmp_path, "sleep") == []
    assert _calls(tmp_path, "id") == []


def test_comment_is_posted_to_this_repository(tmp_path: Path) -> None:
    """The comment goes to the numbered PR of this repository, from a file."""
    result = _run(tmp_path, COMMENT, {"PR_NUMBER": "0012", "MESSAGE": "Hello"})

    assert result.returncode == 0, result.stderr
    arguments = _calls(tmp_path, "gh")
    assert arguments[:5] == ["pr", "comment", "12", "--repo", "octo/repo"]
    assert arguments[5] == "--body-file"


def test_message_is_data_not_shell(tmp_path: Path) -> None:
    """A message holding shell syntax reaches the body file verbatim."""
    message = "$(id) `id` ${{ github.token }}; id\nsecond line"
    capture = tmp_path / "body.txt"

    result = _run(
        tmp_path,
        COMMENT,
        {"PR_NUMBER": "12", "MESSAGE": message, "CAPTURE": str(capture)},
        gh_stub='#!/usr/bin/env bash\ncp "${@: -1}" "$CAPTURE"\n',
    )

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path, "id") == []
    assert capture.read_text(encoding="utf-8") == message


DOC = Path(__file__).resolve().parents[2] / "docs/delayed-pr-comment-workflow.md"
_PIN = "0123456789abcdef0123456789abcdef01234567"
_INPUT_REFERENCE = re.compile(r"\$\{\{\s*inputs\.(?P<name>[A-Za-z0-9_]+)\s*\}\}")


def _document(path: Path) -> dict[str, typ.Any]:
    """Return the parsed workflow at *path*."""
    return typ.cast("dict[str, typ.Any]", load_workflow(path))


def _triggers(document: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the ``on`` mapping, which YAML 1.1 may read as the key ``True``."""
    return document["on"] if "on" in document else document[True]


def _declared_inputs() -> dict[str, typ.Any]:
    """Return the reusable workflow's ``workflow_call`` inputs."""
    return _triggers(_document(WORKFLOW))["workflow_call"]["inputs"]


def _caller_job() -> tuple[dict[str, typ.Any], dict[str, typ.Any]]:
    """Return the published caller's dispatch inputs and its one job."""
    source = load_tested_examples(DOC)["delayed-pr-comment-caller"]
    parsed = yaml.safe_load(source.replace("<commit-sha>", _PIN))
    dispatch = _triggers(parsed)["workflow_dispatch"]["inputs"]
    (job,) = parsed["jobs"].values()
    return dispatch, job


def test_the_workflow_wires_every_input_to_its_own_variable() -> None:
    """Each step's ``env:`` is exactly the expected mapping, so a swap fails."""
    document = _document(WORKFLOW)
    job = document["jobs"]["delay_and_comment"]
    steps = {step["name"]: step for step in job["steps"]}

    assert list(steps) == [WAIT, COMMENT]
    assert steps[WAIT]["env"] == {
        "PR_NUMBER": "${{ inputs.pr_number }}",
        "DELAY_MINUTES": "${{ inputs.delay_minutes }}",
    }
    assert steps[COMMENT]["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr_number }}",
        "MESSAGE": "${{ inputs.message }}",
    }
    assert job["permissions"] == {"pull-requests": "write"}


def test_the_numeric_inputs_are_strings() -> None:
    """A number-typed input fails when the API dispatches it, so none is declared."""
    declared = _declared_inputs()

    assert {name: spec["type"] for name, spec in declared.items()} == {
        "pr_number": "string",
        "delay_minutes": "string",
        "message": "string",
    }


def test_the_timeout_leaves_room_above_the_delay_cap() -> None:
    """The job timeout exceeds the delay cap and stays under GitHub's limit."""
    job = _document(WORKFLOW)["jobs"]["delay_and_comment"]
    cap = int(re.search(r"max_delay=(\d+)", _step_script(WAIT))[1])  # type: ignore[index]

    assert cap + 5 <= job["timeout-minutes"] < 360


def test_the_published_caller_satisfies_the_reusable_workflow() -> None:
    """The documented caller supplies every input, from its own dispatch inputs."""
    dispatch, job = _caller_job()
    declared = _declared_inputs()

    assert set(dispatch) == set(declared)
    assert job["with"] == {name: f"${{{{ inputs.{name} }}}}" for name in declared}
    assert job["permissions"] == {"pull-requests": "write"}
    assert re.fullmatch(
        r"leynos/shared-actions/\.github/workflows/delayed-pr-comment\.yml@[0-9a-f]{40}"
        r"(?: #.*)?",
        job["uses"],
    )


def test_an_unmarked_fence_is_refused(tmp_path: Path) -> None:
    """A fence with no marker before it is an error, not a skipped example."""
    document = tmp_path / "doc.md"
    document.write_text("text\n\n```yaml\na: 1\n```\n", encoding="utf-8")

    with pytest.raises(ValueError, match="without a tested-example marker"):
        load_tested_examples(document)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("~~~yaml\na: 1\n~~~\n", "without a tested-example", id="tilde"),
        pytest.param(
            "<!-- tested-example: a -->\n````yaml\nx\n```\n",
            "unterminated fence",
            id="short-close",
        ),
        pytest.param(
            "<!-- tested-example: a -->\n```\nx\n```\n"
            "<!-- tested-example: a -->\n```\ny\n```\n",
            "duplicate example",
            id="duplicate",
        ),
    ],
)
def test_the_loader_refuses_what_it_cannot_vouch_for(
    tmp_path: Path, text: str, message: str
) -> None:
    """Tilde fences, short closers and repeated identifiers are errors."""
    document = tmp_path / "doc.md"
    document.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_tested_examples(document)


def test_a_marker_survives_a_blank_line_and_a_longer_closer_is_accepted(
    tmp_path: Path,
) -> None:
    """A blank line keeps the marker; a longer closing fence closes the block."""
    document = tmp_path / "doc.md"
    document.write_text(
        "<!-- tested-example: a -->\n\n```yaml\nx: 1\n`````\n", encoding="utf-8"
    )

    assert load_tested_examples(document) == {"a": "x: 1\n"}


def test_text_between_a_marker_and_its_fence_clears_the_marker(
    tmp_path: Path,
) -> None:
    """A marker names the very next block, not a fence further down."""
    document = tmp_path / "doc.md"
    document.write_text(
        "<!-- tested-example: a -->\ntext\n```yaml\nx: 1\n```\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="without a tested-example marker"):
        load_tested_examples(document)
