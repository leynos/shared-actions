"""Behaviour of the reusable delayed PR comment workflow's shell steps.

The two ``run:`` bodies are read from the workflow and executed under bash
with stub ``sleep`` and ``gh`` commands first on ``PATH``. The stubs record
their arguments, so each case proves what ran, not what the file says.

Run via ``make test``.
"""

from __future__ import annotations

import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

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
