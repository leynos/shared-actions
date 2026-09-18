"""Tests for the cargo watchdog proof.

The proof itself is a lane, but its verdict is a pure function of what
one run did, so the interesting cases are testable here. Every check is
exercised in both directions: a proof that cannot fail proves nothing,
and one that fails on a healthy run stops the repository.
"""

from __future__ import annotations

import dataclasses as dc
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from workflow_scripts.prove_cargo_watchdog import (
    CARGO_MARKER,
    INVALID_BUDGETS,
    TERMINATION_MESSAGE,
    TERMINATION_OVERHEAD_SECONDS,
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


def _parses_as_float(value: str) -> bool:
    """Return True when `float()` accepts *value*."""
    try:
        float(value)
    except ValueError:
        return False
    return True


#: The healthy outcomes the cases below vary one field of. `dc.replace`
#: takes the place of a dictionary and a cast: it checks the field names
#: and their types, so a case that misspells a field or gives it the
#: wrong type fails here rather than constructing an `Outcome` nobody
#: meant.
HEALTHY_TERMINATION: typ.Final[Outcome] = Outcome(
    returncode=1,
    seconds=BUDGET + 0.5,
    output=TERMINATED_OUTPUT,
)

HEALTHY_REFUSAL: typ.Final[Outcome] = Outcome(
    returncode=1,
    seconds=0.4,
    output=(
        f"::error::{WATCHDOG_VARIABLE} must be a finite number of seconds "
        "greater than zero; got '0'\n"
    ),
)


class TestCargoWatchdogProof:
    """The verdicts the proof reaches, and the run it reaches them from."""

    def test_a_healthy_termination_is_accepted(self) -> None:
        """The proof passes the run it exists to describe.

        Without this, every rule below could be satisfied by a check that
        refuses everything.
        """
        failures = check_termination(
            HEALTHY_TERMINATION, budget=BUDGET, sleep_seconds=SLEEP
        )

        assert failures == [], (
            f"a healthy termination was rejected for {failures}; the proof would "
            "then fail every run and prove nothing about the watchdog"
        )

    def test_a_healthy_refusal_is_accepted(self) -> None:
        """The refusal rule passes a genuine refusal."""
        failures = check_refusal(HEALTHY_REFUSAL, value="0")

        assert failures == [], (
            f"a healthy refusal was rejected for {failures}; the proof would then "
            "fail a runner that refuses an invalid budget exactly as it should"
        )

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
            pytest.param(
                {"seconds": BUDGET + TERMINATION_OVERHEAD_SECONDS + 1},
                "fired late rather than on the budget",
                id="fired-far-past-the-budget",
            ),
            pytest.param(
                {"seconds": SLEEP - 1},
                "fired late rather than on the budget",
                id="fired-just-under-the-sleep",
            ),
        ],
    )
    def test_each_way_a_termination_can_be_wrong_is_named(
        self, overrides: dict[str, object], expected: str
    ) -> None:
        """A partial regression names itself rather than reading as a pass.

        The four properties fail independently. A watchdog that kills but
        reports success, one that waits cargo out, one that never reaches
        cargo at all, and one that discards the log are different defects,
        and a proof that collapsed them into one verdict would send whoever
        reads it looking in the wrong place.
        """
        failures = check_termination(
            dc.replace(HEALTHY_TERMINATION, **overrides),
            budget=BUDGET,
            sleep_seconds=SLEEP,
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
            pytest.param(
                {
                    "output": (
                        f"::error::{WATCHDOG_VARIABLE} must be a finite "
                        "number of seconds greater than zero\n"
                    )
                },
                "does not quote",
                id="refusal-names-the-setting-but-not-the-value",
            ),
        ],
    )
    def test_each_way_a_refusal_can_be_wrong_is_named(
        self, overrides: dict[str, object], expected: str
    ) -> None:
        """A zero budget read as "no watchdog" is the failure being refused.

        A run that accepts zero, or that refuses without naming the setting,
        or that starts cargo anyway, each leaves a consumer running
        unguarded while appearing to declare a budget.

        Naming the setting without quoting the value is its own defect. A
        job that sets a budget in more than one place gets a message it
        cannot act on, and the guide promises both.
        """
        failures = check_refusal(dc.replace(HEALTHY_REFUSAL, **overrides), value="0")

        assert any(expected in failure for failure in failures), failures

    def test_every_documented_invalid_class_is_exercised(self) -> None:
        """The proof covers each class the runner documents as invalid.

        Restated here rather than derived, because the integration test
        builds its expectations from the same tuple and so cannot notice
        the tuple being truncated. `_read_wait_timeout` refuses three
        kinds of value: one `float()` cannot parse, one that is not
        finite, and one that is not greater than zero. Dropping a class
        here leaves a branch of that validator unproved, which is how
        the earlier version of this lane tested only zero.
        """
        values = {value for value, _ in INVALID_BUDGETS}

        assert {"0", "-1"} <= values, (
            f"the non-positive class needs zero and a negative value; the "
            f"proof exercises {sorted(values)}"
        )
        assert {"nan", "inf"} <= values, (
            f"the non-finite class needs both spellings; the proof exercises "
            f"{sorted(values)}"
        )
        assert any(
            value not in {"0", "-1", "nan", "inf"} and not _parses_as_float(value)
            for value in values
        ), (
            f"the unparseable class needs a value float() rejects; the proof "
            f"exercises {sorted(values)}"
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="the fake cargo is a POSIX shell script, and the lane is Linux",
    )
    def test_the_proof_passes_against_the_real_runner(self) -> None:
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
        assert "an overrunning cargo is terminated" in stdout, stdout
        missing = [
            value
            for value, _ in INVALID_BUDGETS
            if f"({value!r}) is refused" not in stdout
        ]
        assert not missing, (
            f"the proof reported no verdict for {missing}; every documented "
            "class of invalid budget must be exercised against the real "
            f"runner, and the output was:\n{stdout}"
        )
