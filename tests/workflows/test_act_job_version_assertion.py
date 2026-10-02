"""Contract that the act lane's version assertion fails closed and loudly.

`ci.yml::act-workflows` pins act v0.2.89 through `install-tool`, then
asserts the resolved binary is that version before handing it to
`make test-act`. The assertion is what stops a runner's own act, earlier
on PATH, from quietly running the lane on a version nobody pinned.

An earlier shape wrote it as a bare `[[ ... ]]` under `set -e`: a
mismatch aborted the step with no message, so the one failure the
assertion exists to explain — a runner with the wrong act — was the one
that said nothing about what it found. The step now reports the expected
version, the binary it resolved, and the version that binary printed.

The script is executed here, with stub `act` and `make` binaries, rather
than matched: the property is what the step does, and only running it
shows that a mismatch stops before `make` while a match proceeds.
"""

from __future__ import annotations

import os
import re
import stat
import typing as typ

import pytest
from plumbum import local

from test_support.plumbum_helpers import run_plumbum_command

from . import _workflow_reading as reading

if typ.TYPE_CHECKING:
    from pathlib import Path

WORKFLOW: typ.Final[str] = "ci.yml"
JOB: typ.Final[str] = "act-workflows"
STEP: typ.Final[str] = "Run the act workflow lane"

#: The version the fixture pretends `install-tool` pinned.
PINNED: typ.Final[str] = "0.2.89"


def _step_script() -> str:
    """Return the act lane step's `run` script.

    Raises
    ------
    AssertionError
        If the job or the step, or the step's script, is not found. A
        renamed step would otherwise leave this contract testing nothing.
    """
    job = dict(reading.jobs(WORKFLOW))[JOB]
    steps = list(job.get("steps") or [])
    found = [step for step in steps if step.get("name") == STEP]
    if len(found) != 1:
        msg = f"{WORKFLOW}::{JOB} must have exactly one {STEP!r} step"
        raise AssertionError(msg)
    script = found[0].get("run")
    if not isinstance(script, str) or not script.strip():
        msg = f"{WORKFLOW}::{JOB}::{STEP} carries no run script"
        raise AssertionError(msg)
    return script


def _stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable *name* into *bin_dir* running *body*."""
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_step(tmp_path: Path, version_output: str) -> tuple[int, str, bool]:
    """Run the assertion step against a stub act reporting *version_output*.

    Returns the exit status, the combined output, and whether the stub
    `make` was reached — the marker whose absence is what proves the
    step stopped at the assertion.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "make-ran"
    _stub(bin_dir, "act", f'echo "{version_output}"')
    _stub(bin_dir, "make", f'touch "{marker}"')
    result = run_plumbum_command(
        local["bash"]["-c", _step_script()],
        method="run",
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "ACT_VERSION": PINNED,
        },
    )
    return result.returncode, str(result.stdout) + str(result.stderr), marker.exists()


@pytest.mark.skipif(
    os.name == "nt", reason="the step is bash; the Windows job runs a different one"
)
def test_a_wrong_version_stops_before_make_and_says_what_it_found(
    tmp_path: Path,
) -> None:
    """A mismatch fails the step with the expected and found versions named."""
    code, output, make_ran = _run_step(tmp_path, "act version 0.2.88")

    assert code != 0, "a runner with the wrong act must fail the step"
    assert not make_ran, (
        "the lane ran on a version nobody pinned; the assertion did not stop it"
    )
    assert PINNED in output, (
        f"the failure does not name the expected version:\n{output}"
    )
    assert "0.2.88" in output, f"the failure does not name what it found:\n{output}"


@pytest.mark.skipif(
    os.name == "nt", reason="the step is bash; the Windows job runs a different one"
)
def test_the_pinned_version_proceeds_to_the_lane(tmp_path: Path) -> None:
    """The assertion is a gate, not a blocker: the right version runs the lane."""
    code, output, make_ran = _run_step(tmp_path, f"act version {PINNED}")

    assert code == 0, f"the pinned version failed the step:\n{output}"
    assert make_ran, "the lane was never handed to make on the pinned version"


def test_the_assertion_reads_the_pinned_version_from_the_job() -> None:
    """The version asserted is the one `install-tool` was told to install.

    Read from the job's own `env` rather than compared to a literal here:
    a job that installed one version and asserted another would fail every
    run, and this says which of the two moved.
    """
    job = dict(reading.jobs(WORKFLOW))[JOB]
    environment = job.get("env") or {}
    if not isinstance(environment, dict):  # pragma: no cover - malformed fixture
        msg = f"{WORKFLOW}::{JOB} env is not a mapping: {environment!r}"
        raise TypeError(msg)
    version = str(environment.get("ACT_VERSION", ""))
    script = _step_script()

    assert re.search(r"\$\{ACT_VERSION\}", script), (
        "the step no longer reads ACT_VERSION, so the assertion and the "
        "install can drift apart"
    )
    assert version == PINNED, (
        f"{WORKFLOW}::{JOB} pins act {version!r}; this contract and the "
        f"install step must agree on {PINNED!r}"
    )
    install = [
        step
        for step in list(job.get("steps") or [])
        if step.get("name") == "Install act"
    ]
    assert install, f"{WORKFLOW}::{JOB} lost its act install step"
    assert install[0]["with"]["version"] == "${{ env.ACT_VERSION }}", (
        "the install step and the assertion must read one ACT_VERSION"
    )
