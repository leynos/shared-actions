"""Contracts the two telemetry actions hold to, proved by mutation.

The finish action prints exactly one marker line, which the collector greps
out of the job log. The marker must never appear verbatim in a manifest or in
an echoed command, or the log would carry a second, bogus match; the usage
example must run finish last and under ``if: always()``, or a failed job loses
its record; and nothing may take a value from a secret.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import yaml

ACTIONS = Path(__file__).resolve().parents[2]
FINISH = ACTIONS / "ci-telemetry-finish"
START = ACTIONS / "ci-telemetry-start"
MARKER = "DF12_" + "CI_TELEMETRY"
FENCE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _manifest(directory: Path) -> dict[str, typ.Any]:
    return yaml.safe_load((directory / "action.yml").read_text(encoding="utf-8"))


def _echoed_text(directory: Path) -> str:
    """Return everything a step could print: its run, env and with values."""
    parts: list[str] = []
    for step in _manifest(directory)["runs"]["steps"]:
        parts.append(str(step.get("run", "")))
        parts.extend(str(v) for v in step.get("env", {}).values())
        parts.extend(str(v) for v in step.get("with", {}).values())
    return "\n".join(parts)


def _usage_steps() -> list[dict[str, typ.Any]]:
    """Return the steps of the README's first usage example."""
    readme = (FINISH / "README.md").read_text(encoding="utf-8")
    blocks = [yaml.safe_load(block) for block in FENCE.findall(readme)]
    example = next(b for b in blocks if isinstance(b, dict) and "jobs" in b)
    (job,) = example["jobs"].values()
    return job["steps"]


def test_marker_never_appears_verbatim_in_an_echoed_command_or_script() -> None:
    """The runner echoes step commands; the marker must be built, not written."""
    for directory in (START, FINISH):
        assert MARKER not in _echoed_text(directory), directory
        for path in sorted((directory / "scripts").glob("*.py")):
            assert MARKER not in path.read_text(encoding="utf-8"), path


def test_finish_is_a_single_composite_step_that_always_runs() -> None:
    """One step, no condition of its own, so it runs wherever the caller does."""
    manifest = _manifest(FINISH)
    assert manifest["runs"]["using"] == "composite"
    (step,) = manifest["runs"]["steps"]
    assert "if" not in step
    assert step["id"] == "finish"
    assert set(manifest["outputs"]) == {"record-path", "sampler"}


def test_finish_runs_the_script_as_its_only_command() -> None:
    """The step's last command is the script; failures are never swallowed."""
    (step,) = _manifest(FINISH)["runs"]["steps"]
    commands = [ln.strip() for ln in step["run"].splitlines() if ln.strip()]
    assert commands[-1] == '"${py}" "${CT_FINISH}"'


def test_usage_example_runs_finish_last_and_under_always() -> None:
    """A failed or cancelled job still prints its record."""
    steps = _usage_steps()
    uses = [str(step.get("uses", "")) for step in steps]
    start = next(i for i, u in enumerate(uses) if "ci-telemetry-start" in u)
    end = next(i for i, u in enumerate(uses) if "ci-telemetry-finish" in u)
    assert start < end
    assert end == len(steps) - 1
    assert steps[end]["if"] == "always()"


def test_no_manifest_reads_a_secret() -> None:
    """Records carry runner facts only: no secrets context in either action."""
    for directory in (START, FINISH):
        text = (directory / "action.yml").read_text(encoding="utf-8")
        assert "secrets." not in text, directory
        assert "github.token" not in text, directory


def test_start_step_passes_only_its_own_inputs() -> None:
    """The start step's environment is its inputs and its own script path."""
    (step,) = _manifest(START)["runs"]["steps"]
    assert set(step["env"]) == {"CT_INTERVAL_SECONDS", "CT_DISK_PATH", "CT_SAMPLER"}


def test_state_directory_agrees_between_the_actions() -> None:
    """Start and finish name the same directory under RUNNER_TEMP."""
    pattern = re.compile(r'/ "ci-telemetry"')
    for directory in (START, FINISH):
        scripts = "".join(
            p.read_text(encoding="utf-8") for p in (directory / "scripts").glob("*.py")
        )
        assert pattern.search(scripts), directory
