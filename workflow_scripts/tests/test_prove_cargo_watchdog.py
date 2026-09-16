"""Tests for the cargo watchdog proof.

The proof itself is a lane, but its verdict is a pure function of what
one run did, so the interesting cases are testable here. Every check is
exercised in both directions: a proof that cannot fail proves nothing,
and one that fails on a healthy run stops the repository.
"""

from __future__ import annotations

import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from workflow_scripts.prove_cargo_watchdog import (
    CARGO_MARKER,
    TERMINATION_MESSAGE,
    WATCHDOG_VARIABLE,
    Outcome,
    check_refusal,
    check_termination,
)

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
PROOF: typ.Final[Path] = (
    REPOSITORY_ROOT / "workflow_scripts" / "prove_cargo_watchdog.py"
)
RUNNER: typ.Final[Path] = (
    REPOSITORY_ROOT
    / ".github"
    / "actions"
    / "generate-coverage"
    / "scripts"
    / "run_rust.py"
)

BUDGET: typ.Final[float] = 2.0
SLEEP: typ.Final[float] = 120.0

#: What the runner's output looks like when the watchdog did its job.
TERMINATED_OUTPUT: typ.Final[str] = (
    f"{CARGO_MARKER}: llvm-cov nextest\n"
    f"::error::cargo {TERMINATION_MESSAGE} {BUDGET}s; killing.\n"
)


def _terminated(**overrides: object) -> Outcome:
    """Return the outcome of a healthy termination, with fields replaced."""
    fields: dict[str, object] = {
        "returncode": 1,
        "seconds": BUDGET + 0.5,
        "output": TERMINATED_OUTPUT,
    }
    fields.update(overrides)
    return Outcome(**typ.cast("typ.Any", fields))


def _refused(**overrides: object) -> Outcome:
    """Return the outcome of a healthy refusal, with fields replaced."""
    fields: dict[str, object] = {
        "returncode": 1,
        "seconds": 0.4,
        "output": (
            f"::error::{WATCHDOG_VARIABLE} must be a finite number of seconds "
            "greater than zero; got '0'\n"
        ),
    }
    fields.update(overrides)
    return Outcome(**typ.cast("typ.Any", fields))


def test_a_healthy_termination_is_accepted() -> None:
    """The proof passes the run it exists to describe.

    Without this, every rule below could be satisfied by a check that
    refuses everything.
    """
    assert check_termination(_terminated(), budget=BUDGET, sleep_seconds=SLEEP) == []


def test_a_healthy_refusal_is_accepted() -> None:
    """The refusal rule passes a genuine refusal."""
    assert check_refusal(_refused(), value="0") == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {"returncode": 0},
            "must fail the step",
            id="killed-but-reported-success",
        ),
        pytest.param(
            {"seconds": SLEEP + 1},
            "rather than terminated",
            id="waited-cargo-out",
        ),
        pytest.param(
            {"seconds": 0.1},
            "did not run cargo under the watchdog",
            id="never-reached-cargo",
        ),
        pytest.param(
            {"output": f"::error::cargo {TERMINATION_MESSAGE} 2.0s; killing.\n"},
            "leaves nothing to act on",
            id="log-discarded",
        ),
        pytest.param(
            {"output": f"{CARGO_MARKER}: llvm-cov nextest\nsegmentation fault\n"},
            "some reason other than the watchdog",
            id="died-for-another-reason",
        ),
    ],
)
def test_each_way_a_termination_can_be_wrong_is_named(
    overrides: dict[str, object], expected: str
) -> None:
    """A partial regression names itself rather than reading as a pass.

    The four properties fail independently. A watchdog that kills but
    reports success, one that waits cargo out, one that never reaches
    cargo at all, and one that discards the log are different defects,
    and a proof that collapsed them into one verdict would send whoever
    reads it looking in the wrong place.
    """
    failures = check_termination(
        _terminated(**overrides), budget=BUDGET, sleep_seconds=SLEEP
    )

    assert any(expected in failure for failure in failures), failures


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {"returncode": 0},
            "must be refused",
            id="accepted-a-zero-budget",
        ),
        pytest.param(
            {"output": "::error::bad value\n"},
            "cannot tell which setting was wrong",
            id="refusal-names-nothing",
        ),
        pytest.param(
            {
                "output": (
                    f"::error::{WATCHDOG_VARIABLE} is wrong\n"
                    f"{CARGO_MARKER}: llvm-cov nextest\n"
                )
            },
            "unguarded rather than stopped",
            id="cargo-ran-anyway",
        ),
    ],
)
def test_each_way_a_refusal_can_be_wrong_is_named(
    overrides: dict[str, object], expected: str
) -> None:
    """A zero budget read as "no watchdog" is the failure being refused.

    A run that accepts zero, or that refuses without naming the setting,
    or that starts cargo anyway, each leaves a consumer running
    unguarded while appearing to declare a budget.
    """
    failures = check_refusal(_refused(**overrides), value="0")

    assert any(expected in failure for failure in failures), failures


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake cargo is a POSIX shell script, and the lane is Linux",
)
def test_the_proof_passes_against_the_real_runner() -> None:
    """The lane's own command succeeds against the action as it stands.

    This is the part no unit test covers: that the fixture satisfies the
    runner's manifest gate, that the fake cargo is the one it finds, and
    that the watchdog really terminates the run. Its sleep is shortened
    so the test costs seconds; the lane uses the full one.

    Skipped on Windows. The fake cargo carries a shebang and an
    executable bit, neither of which Windows honours, so the runner
    would find the real cargo or none at all and the proof would fail
    for a reason that says nothing about the watchdog. The lane itself
    is Linux, so nothing goes unproved.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")

    proof = local[shutil.which("uv") or "uv"][
        "run",
        "--script",
        str(PROOF),
        "--runner",
        str(RUNNER),
        "--sleep-seconds",
        "20",
    ]
    with local.cwd(REPOSITORY_ROOT):
        returncode, stdout, stderr = proof.run(retcode=None, timeout=300)

    assert returncode == 0, stdout + stderr
    assert "an overrunning cargo is terminated" in stdout
    assert "a zero budget is refused" in stdout
