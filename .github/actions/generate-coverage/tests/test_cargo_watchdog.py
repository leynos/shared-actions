"""Tests for the cargo watchdog's budget and how it reports itself.

The watchdog kills cargo after a budget, and exists to catch a hang. Everything
here is about the distinction between a hang and a build that is merely cold,
which is invisible from outside the process: where the budget comes from, that
it is reported before cargo runs, and that an expiry says which of the two it
was.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from _coverage_test_support import _load_module


def test_cargo_watchdog_defaults_to_a_cold_store_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default budget must fit a run that compiles everything.

    A lane that no longer archives its ``target`` tree does the whole
    instrumented compile inside this budget on the first run of a branch and
    after every cache eviction. Netsuke measured 512 s to finish its tests and
    was killed at 600 s during report generation, with sccache at 19% hits.
    """
    mod = _load_module(monkeypatch, "run_rust")
    # Both sources, not just the variable: an inherited input would make this
    # assert the ambient environment rather than the default.
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.delenv("INPUT_CARGO_WAIT_TIMEOUT", raising=False)

    runner = mod._cargo_runner
    assert runner.DEFAULT_CARGO_WAIT_TIMEOUT == 1800.0, (
        f"the default budget is {runner.DEFAULT_CARGO_WAIT_TIMEOUT}s"
    )
    assert runner._resolve_wait_timeout() == 1800.0


@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.parametrize(
    "name", ["RUN_RUST_CARGO_WAIT_TIMEOUT", "INPUT_CARGO_WAIT_TIMEOUT"]
)
def test_cargo_watchdog_falls_back_when_a_variable_is_blank(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """An unset action input arrives as an empty string, not as an absent one."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.delenv("INPUT_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.setenv(name, value)

    runner = mod._cargo_runner
    assert runner._resolve_wait_timeout() == runner.DEFAULT_CARGO_WAIT_TIMEOUT


def test_a_job_level_budget_beats_the_step_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller's job-level budget must survive a step passing the input.

    The variable is how a caller sets one budget across several steps, and it
    is what consumers already use. A step-level input default that quietly
    replaced it would cut such a caller back without saying so.
    """
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setenv("RUN_RUST_CARGO_WAIT_TIMEOUT", "3600")
    monkeypatch.setenv("INPUT_CARGO_WAIT_TIMEOUT", "1800")

    assert mod._cargo_runner._resolve_wait_timeout() == 3600.0


def test_the_input_is_used_when_no_variable_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no job-level budget, the step's input decides."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.setenv("INPUT_CARGO_WAIT_TIMEOUT", "900")

    assert mod._cargo_runner._resolve_wait_timeout() == 900.0


@pytest.mark.parametrize(
    "name", ["RUN_RUST_CARGO_WAIT_TIMEOUT", "INPUT_CARGO_WAIT_TIMEOUT"]
)
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "-0.5"])
def test_cargo_watchdog_rejects_a_budget_that_cannot_expire_usefully(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """A budget must be finite and positive, whichever source names it.

    Now that this is a public input, `float()` accepting a value is not enough.
    A NaN deadline never compares greater than the clock, so the watchdog never
    fires and the pump loop spins; an infinite one reaches the platform's own
    timeout handling; a non-positive one kills a healthy build immediately.
    """
    mod = _load_module(monkeypatch, "run_rust")
    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        mod.typer,
        "echo",
        lambda message, err=False: messages.append((message, err)),
    )
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.delenv("INPUT_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(mod.typer.Exit):
        mod._cargo_runner._resolve_wait_timeout()

    errors = [text for text, is_error in messages if is_error]
    assert len(errors) == 1, f"expected one error, got {errors}"
    assert name in errors[0], f"the rejection does not name {name}: {errors[0]!r}"
    assert "greater than zero" in errors[0], errors[0]


@pytest.mark.parametrize(
    "name", ["RUN_RUST_CARGO_WAIT_TIMEOUT", "INPUT_CARGO_WAIT_TIMEOUT"]
)
def test_cargo_watchdog_rejects_a_budget_that_is_not_a_number(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """A non-numeric budget is refused, naming the source that supplied it."""
    mod = _load_module(monkeypatch, "run_rust")
    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        mod.typer,
        "echo",
        lambda message, err=False: messages.append((message, err)),
    )
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.delenv("INPUT_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.setenv(name, "not-a-float")

    with pytest.raises(mod.typer.Exit):
        mod._cargo_runner._resolve_wait_timeout()

    assert (f"::error::{name} must be a number", True) in messages, messages


def test_cargo_watchdog_reports_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget belongs in the log, so an expiry can be read against it."""
    mod = _load_module(monkeypatch, "run_rust")
    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        mod.typer,
        "echo",
        lambda message, err=False: messages.append((message, err)),
    )
    monkeypatch.delenv("INPUT_CARGO_WAIT_TIMEOUT", raising=False)
    monkeypatch.setenv("RUN_RUST_CARGO_WAIT_TIMEOUT", "900")

    assert mod._cargo_runner._resolve_wait_timeout() == 900.0
    assert ("cargo watchdog budget: 900.0s", False) in messages, messages


def test_cargo_wait_timeout_input_reaches_the_runner() -> None:
    """The input must be wired to the variable the runner reads.

    An input the script never sees is worse than no input: it looks
    configurable and silently is not.
    """
    manifest_path = Path(__file__).resolve().parents[1] / "action.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    declared = manifest["inputs"]["cargo-wait-timeout"]
    assert declared["required"] is False, "cargo-wait-timeout must stay optional"
    assert declared["default"] == "1800", (
        f"the documented default is {declared['default']!r}"
    )

    rust_steps = [
        step
        for step in manifest["runs"]["steps"]
        if "run_rust.py" in str(step.get("run", ""))
    ]
    assert len(rust_steps) == 1, "expected exactly one run_rust.py step"
    env = rust_steps[0]["env"]
    assert env["INPUT_CARGO_WAIT_TIMEOUT"] == "${{ inputs.cargo-wait-timeout }}", (
        "the input does not reach the script"
    )
    assert "RUN_RUST_CARGO_WAIT_TIMEOUT" not in env, (
        "assigning RUN_RUST_CARGO_WAIT_TIMEOUT here would clobber a caller's "
        "job-level budget with this step's input default"
    )
