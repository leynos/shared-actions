#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "plumbum>=1.9,<2.0"]
# ///

"""Prove that the coverage action's cargo watchdog terminates a run.

`generate-coverage` wraps `cargo` in a wall-clock watchdog read from
``RUN_RUST_CARGO_WAIT_TIMEOUT`` or the ``cargo-wait-timeout`` input.
Three things about it are load bearing for every consumer that adopts
the action, and none of them can be proved by a consumer:

- a `cargo` invocation exceeding the budget is actually terminated;
- the step then fails, rather than passing with the work unfinished;
- the log written before the watchdog fired survives, because the
  report is the only thing that makes an overrun actionable.

A consumer lane that deliberately overruns its own job ceiling proves
GitHub's cancellation rather than the value we chose, and costs runner
minutes on every pull request to say so. This proves the watchdog once,
in the repository that owns it, in seconds: a fake `cargo` on ``PATH``
that announces itself and then sleeps far longer than the budget.

The refusal is proved beside it. A non-positive or unparseable budget
is rejected before `cargo` starts, rather than read as "no watchdog",
so a lane carrying zero fails loudly instead of running unguarded while
appearing to declare a budget.

Usage
-----
    prove_cargo_watchdog.py --runner path/to/run_rust.py

Exits zero when every case holds, and one otherwise, naming each
failure.
"""

from __future__ import annotations

import dataclasses as dc
import os
import shutil
import tempfile
import time
import typing as typ
from pathlib import Path

import cyclopts
from plumbum import local

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

app = cyclopts.App(
    name="prove-cargo-watchdog",
    help="Prove the coverage action's cargo watchdog terminates a run.",
)

#: Written by the fake cargo before it sleeps. Its presence in the
#: captured output is the evidence that the log survives termination;
#: its absence is the evidence that cargo was never started.
CARGO_MARKER: typ.Final[str] = "fake-cargo-started"

#: The variable the runner reads first, and names in its refusal.
WATCHDOG_VARIABLE: typ.Final[str] = "RUN_RUST_CARGO_WAIT_TIMEOUT"

#: Text the watchdog's own termination message must carry, so that the
#: proof fails if the run dies for some other reason with the same
#: status. Matched on the part that states the cause rather than on the
#: whole sentence, which names a budget that varies.
TERMINATION_MESSAGE: typ.Final[str] = "did not exit within"

_FIXTURE_MANIFEST: typ.Final[str] = """\
[package]
name = "cargo-watchdog-fixture"
version = "0.0.0"
edition = "2021"
"""

_FIXTURE_MAIN: typ.Final[str] = "fn main() {}\n"

_FAKE_CARGO: typ.Final[str] = f"""\
#!/usr/bin/env bash
# Announce the run, then outlast any budget this proof would set.
echo "{CARGO_MARKER}: $*"
sleep "${{FAKE_CARGO_SLEEP_SECONDS:-120}}"
"""


def _uv() -> str:
    """Return the `uv` executable, failing with a usable message without it."""
    found = shutil.which("uv")
    if found is None:
        message = "uv is not on PATH; the coverage runner is a uv script"
        raise SystemExit(message)
    return found


#: Every class of budget `_read_wait_timeout` documents as invalid, with
#: the name of the class, so that a report says which one was accepted.
#: Zero alone proved only the non-positive branch; a validator that lost
#: its finiteness check or its `float()` guard would have kept passing.
#:
#: A blank value is deliberately absent. The runner reads blank as "not
#: set" and falls back to the default budget, so a lane carrying one is
#: guarded rather than unguarded, and demanding a refusal here would
#: contradict the runner's documented behaviour.
#: How far past the budget a run may end and still count as the watchdog
#: firing. Without an upper bound the only rejection above the budget is
#: the fake cargo's sleep, so a watchdog that fired a minute late against
#: a two-second budget proved nothing while the report stayed green.
#:
#: Sized from measurement rather than from a round number. The invalid
#: cases measure start-up alone, because they refuse before cargo runs at
#: all: they took 0.6s to 1.2s on a developer host at a load average of
#: 24. The overrun case took 2.6s to 2.8s against the 2.0s budget over
#: four consecutive runs on that host, so the overhead being allowed for
#: is under a second. Fifteen seconds is more than ten times that, which
#: leaves room for a cold runner and for scheduling, and is still an
#: eighth of the sleep, so the bound discriminates rather than merely
#: restating the sleep check.
TERMINATION_OVERHEAD_SECONDS: typ.Final[float] = 15.0

INVALID_BUDGETS: typ.Final[tuple[tuple[str, str], ...]] = (
    ("0", "a zero budget"),
    ("-1", "a negative budget"),
    ("nan", "a budget that is not a number"),
    ("inf", "an infinite budget"),
    ("soon", "a budget that is not a number at all"),
)


@dc.dataclass(frozen=True, slots=True)
class Outcome:
    """What one invocation of the coverage runner did.

    Attributes
    ----------
    returncode : int
        The runner's exit status.
    seconds : float
        Wall time from launching the runner to its exit. The proof turns
        on this: a run that ends at the budget is the watchdog firing,
        and one that ends at the fake cargo's sleep is not.
    output : str
        Standard output and standard error, concatenated, because which
        stream a message arrived on says nothing about the watchdog.
    """

    returncode: int
    seconds: float
    output: str


def check_termination(
    outcome: Outcome, *, budget: float, sleep_seconds: float
) -> list[str]:
    """Return the ways *outcome* fails to prove the watchdog terminated cargo.

    An empty list is the proof. Each of the properties is checked
    separately so a partial regression names itself: a watchdog that
    kills but reports success, or one that reports failure only once
    cargo finished of its own accord, are different defects.

    Parameters
    ----------
    outcome : Outcome
        What the run under an overrunning cargo did.
    budget : float
        The seconds the watchdog was given. A run shorter than this
        never reached cargo.
    sleep_seconds : float
        How long the fake cargo sleeps. A run at least this long waited
        cargo out instead of terminating it.

    Returns
    -------
    list[str]
        One sentence for each property the outcome fails, empty when it
        proves the watchdog fired.
    """
    failures: list[str] = []
    if outcome.returncode == 0:
        failures.append(
            "the runner succeeded; an overrunning cargo must fail the step "
            "rather than pass with the work unfinished"
        )
    if outcome.seconds >= sleep_seconds:
        failures.append(
            f"the runner took {outcome.seconds:.1f}s, which is at least the "
            f"{sleep_seconds:.0f}s the fake cargo sleeps, so cargo was waited "
            "out rather than terminated"
        )
    if outcome.seconds < budget:
        failures.append(
            f"the runner took {outcome.seconds:.1f}s, less than the "
            f"{budget:.1f}s budget, so it did not run cargo under the "
            "watchdog at all"
        )
    ceiling = budget + TERMINATION_OVERHEAD_SECONDS
    if outcome.seconds > ceiling:
        failures.append(
            f"the runner took {outcome.seconds:.1f}s against a {budget:.1f}s "
            f"budget, more than the {ceiling:.1f}s that budget plus "
            f"{TERMINATION_OVERHEAD_SECONDS:.0f}s of start-up allows, so the "
            "watchdog fired late rather than on the budget"
        )
    if CARGO_MARKER not in outcome.output:
        failures.append(
            "the output cargo wrote before the watchdog fired is missing; an "
            "overrun that discards its log leaves nothing to act on"
        )
    if TERMINATION_MESSAGE not in outcome.output:
        failures.append(
            f"the output does not carry {TERMINATION_MESSAGE!r}, so the run "
            "failed for some reason other than the watchdog"
        )
    return failures


def check_refusal(outcome: Outcome, *, value: str) -> list[str]:
    """Return the ways *outcome* fails to prove a bad budget is refused.

    An invalid budget must be refused before cargo starts. Read instead
    as "no watchdog", a lane carrying one would run unguarded while
    appearing to declare a budget, which is the failure this rule is
    for.

    Parameters
    ----------
    outcome : Outcome
        What the run under the invalid budget did.
    value : str
        The budget the run was given, named in the failure sentences so
        that a report says which class of invalid value was accepted.

    Returns
    -------
    list[str]
        One sentence for each property the outcome fails, empty when it
        proves the budget was refused and cargo never started.
    """
    failures: list[str] = []
    if outcome.returncode == 0:
        failures.append(
            f"the runner accepted {WATCHDOG_VARIABLE}={value!r}; a budget "
            "that is not a positive number must be refused"
        )
    if WATCHDOG_VARIABLE not in outcome.output:
        failures.append(
            f"the refusal does not name {WATCHDOG_VARIABLE}, so a caller "
            "cannot tell which setting was wrong"
        )
    if value not in outcome.output:
        failures.append(
            f"the refusal does not quote {value!r}, so a caller reading a "
            "job log cannot tell which of several budgets in the lane was "
            "the one refused"
        )
    if CARGO_MARKER in outcome.output:
        failures.append(
            "cargo ran despite the refusal, so the run was unguarded rather "
            "than stopped"
        )
    return failures


def _write_fixture(root: Path) -> tuple[Path, Path]:
    """Create the fixture project and the fake cargo, and return their paths.

    A manifest is required because the runner's cargo steps are gated on
    one; without it the watchdog never arms and the proof would pass by
    never running.
    """
    project = root / "project"
    (project / "src").mkdir(parents=True)
    manifest = project / "Cargo.toml"
    manifest.write_text(_FIXTURE_MANIFEST, encoding="utf-8")
    (project / "src" / "main.rs").write_text(_FIXTURE_MAIN, encoding="utf-8")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text(_FAKE_CARGO, encoding="utf-8")
    cargo.chmod(0o755)
    return manifest, bin_dir


def _run_case(
    runner: Path,
    root: Path,
    *,
    budget: str,
    sleep_seconds: float,
) -> Outcome:
    """Run the coverage runner once against the fixture, and time it."""
    manifest, bin_dir = _write_fixture(root)
    environment = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_CARGO_SLEEP_SECONDS": str(int(sleep_seconds)),
        "GITHUB_OUTPUT": str(root / "github-output"),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "DETECTED_CARGO_MANIFEST": str(manifest),
        "INPUT_OUTPUT_PATH": str(root / "lcov.info"),
        WATCHDOG_VARIABLE: budget,
    }
    (root / "github-output").write_text("", encoding="utf-8")
    # `uv run --script` because the runner declares its own inline
    # dependencies, which is how the composite action invokes it too.
    runner_command = local[_uv()]["run", "--script", str(runner)]
    started = time.monotonic()
    with local.cwd(manifest.parent), local.env(**environment):
        # The timeout is a backstop for a watchdog that never fires, so a
        # regression fails the proof rather than hanging the lane.
        returncode, stdout, stderr = runner_command.run(
            retcode=None, timeout=sleep_seconds * 2
        )
    return Outcome(
        returncode=returncode,
        seconds=time.monotonic() - started,
        output=stdout + stderr,
    )


def _report(label: str, failures: cabc.Sequence[str], outcome: Outcome) -> None:
    """Print one case's verdict, with its evidence when it failed."""
    if not failures:
        print(f"ok: {label} ({outcome.seconds:.1f}s, exit {outcome.returncode})")
        return
    print(f"FAILED: {label}")
    for failure in failures:
        print(f"  - {failure}")
    print("  captured output:")
    for line in outcome.output.splitlines():
        print(f"    {line}")


@app.default
def main(
    *,
    runner: Path,
    budget: float = 2.0,
    sleep_seconds: float = 120.0,
) -> None:
    """Prove the watchdog terminates an overrun and refuses a bad budget.

    Parameters
    ----------
    runner : Path
        The coverage action's ``run_rust.py``, which is where the
        watchdog lives.
    budget : float
        Seconds to allow cargo. Small on purpose: the proof is that the
        run ends at the budget rather than at the sleep.
    sleep_seconds : float
        How long the fake cargo sleeps. Far longer than the budget, so
        the two outcomes cannot be confused for one another.
    """
    # Resolved because each case runs with the fixture project as its
    # working directory, which is what the runner's manifest detection
    # expects; a relative path would be read against that instead.
    runner = runner.resolve()
    if not runner.is_file():
        message = f"no coverage runner at {runner}"
        raise SystemExit(message)

    verdicts: list[bool] = []
    with tempfile.TemporaryDirectory() as raw:
        overrun_root = Path(raw) / "overrun"
        overrun_root.mkdir()
        overrun = _run_case(
            runner, overrun_root, budget=str(budget), sleep_seconds=sleep_seconds
        )
        failures = check_termination(
            overrun, budget=budget, sleep_seconds=sleep_seconds
        )
        _report(
            "an overrunning cargo is terminated and the step fails",
            failures,
            overrun,
        )
        verdicts.append(not failures)

        for index, (value, label) in enumerate(INVALID_BUDGETS):
            case_root = Path(raw) / f"invalid-{index}"
            case_root.mkdir()
            outcome = _run_case(
                runner, case_root, budget=value, sleep_seconds=sleep_seconds
            )
            failures = check_refusal(outcome, value=value)
            _report(
                f"{label} ({value!r}) is refused before cargo starts",
                failures,
                outcome,
            )
            verdicts.append(not failures)

    if not all(verdicts):
        raise SystemExit(1)


if __name__ == "__main__":
    app()
