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
    monkeypatch.delenv("RUN_RUST_CARGO_WAIT_TIMEOUT", raising=False)

    runner = mod._cargo_runner
    assert runner.DEFAULT_CARGO_WAIT_TIMEOUT == 1800.0, (
        f"the default budget is {runner.DEFAULT_CARGO_WAIT_TIMEOUT}s"
    )
    assert runner._resolve_wait_timeout() == 1800.0


@pytest.mark.parametrize("value", ["", "   "])
def test_cargo_watchdog_falls_back_when_the_variable_is_blank(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """An unset action input arrives as an empty string, not as an absent one."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setenv("RUN_RUST_CARGO_WAIT_TIMEOUT", value)

    runner = mod._cargo_runner
    assert runner._resolve_wait_timeout() == runner.DEFAULT_CARGO_WAIT_TIMEOUT


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
    assert rust_steps[0]["env"]["RUN_RUST_CARGO_WAIT_TIMEOUT"] == (
        "${{ inputs.cargo-wait-timeout }}"
    ), "the input does not reach RUN_RUST_CARGO_WAIT_TIMEOUT"
