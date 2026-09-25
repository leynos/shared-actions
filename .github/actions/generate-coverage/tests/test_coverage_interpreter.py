"""Contracts that pin the interpreter Python coverage is measured on.

Three mechanisms, each held here and each proved by mutating the manifest:

* ``Setup uv`` installs an exact uv release, so a uv release cannot change
  which interpreter a run discovers.
* ``Resolve coverage interpreter`` chooses the interpreter before the baseline
  is restored, and ``run_python.py`` builds the venv on it with
  ``uv venv --python``.
* The ratchet baseline key carries the interpreter's ``major.minor``; that
  half is asserted in ``.github/actions/tests/test_ratchet_baseline_cache.py``
  beside the rest of the key's shape.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml
from _coverage_test_support import _load_module

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"
EXACT_RELEASE = re.compile(r"\d+\.\d+\.\d+")
RESOLVE_STEP = "Resolve coverage interpreter"
PYTHON_LANES = {"python", "mixed"}


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's steps."""
    return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))["runs"]["steps"]


def _step(name: str) -> dict[str, typ.Any]:
    """Return the one step called ``name``."""
    found = [step for step in _steps() if step.get("name") == name]
    assert len(found) == 1, f"expected one {name!r} step, found {len(found)}"
    return found[0]


def _step_by_id(step_id: str) -> dict[str, typ.Any]:
    """Return the one step whose ``id`` is ``step_id``."""
    found = [step for step in _steps() if step.get("id") == step_id]
    assert len(found) == 1, f"expected one step with id {step_id!r}"
    return found[0]


def _lanes_named(condition: str) -> set[str]:
    """Return the detected languages a step condition compares against."""
    return set(re.findall(r"steps\.detect\.outputs\.lang == '(\w+)'", condition))


def test_setup_uv_installs_an_exact_release() -> None:
    """A floating uv changed the interpreter under every Python caller."""
    version = str((_step("Setup uv").get("with") or {}).get("version", ""))
    assert EXACT_RELEASE.fullmatch(version), (
        f"Setup uv must name an exact uv release, got {version!r}"
    )


def test_the_interpreter_is_resolved_before_the_baseline_is_restored() -> None:
    """The baseline key needs the interpreter, so resolution comes first."""
    steps = _steps()
    resolve_at = steps.index(_step(RESOLVE_STEP))
    assert resolve_at < steps.index(_step("Restore baselines"))
    assert resolve_at > steps.index(_step_by_id("detect")), (
        "resolution reads the detected language, so it follows detection"
    )


def test_the_interpreter_is_resolved_for_every_python_lane() -> None:
    """Python and mixed runs both measure Python, so both resolve it."""
    step = _step(RESOLVE_STEP)
    assert step.get("id") == "interpreter"
    assert _lanes_named(str(step.get("if", ""))) == PYTHON_LANES


def test_the_resolver_reads_the_input_and_the_path_interpreter() -> None:
    """The input comes first, and the PATH interpreter is captured before uv."""
    step = _step(RESOLVE_STEP)
    assert (step.get("env") or {}).get("INPUT_PYTHON_VERSION") == (
        "${{ inputs.python-version }}"
    )
    run = str(step.get("run", ""))
    assert "scripts/resolve_python.py" in run
    assert run.index("GC_PATH_PYTHON=") < run.index("uv run"), (
        "the PATH interpreter must be read before `uv run` puts its own on PATH"
    )


def test_the_venv_is_built_on_the_resolved_interpreter() -> None:
    """``run_python.py`` receives the resolver's interpreter path."""
    env = _step_by_id("python").get("env") or {}
    assert env.get("GC_COVERAGE_PYTHON") == "${{ steps.interpreter.outputs.python }}"


def test_python_version_input_defaults_to_empty() -> None:
    """Callers that pin nothing fall through to ``.python-version`` and PATH."""
    inputs = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))["inputs"]
    assert inputs["python-version"].get("default") == ""


@pytest.fixture
def run_python_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_python`` module."""
    return _load_module(monkeypatch, "run_python")


def test_venv_args_name_the_resolved_interpreter(run_python_module: ModuleType) -> None:
    """``uv venv --python`` uses exactly the path the resolver published."""
    args = run_python_module._venv_args({"GC_COVERAGE_PYTHON": "/py/bin/python3.13"})

    assert args == ["venv", "--python", "/py/bin/python3.13", ".venv-coverage"]


@pytest.mark.parametrize("value", ["", "   "])
def test_venv_args_without_an_interpreter_keep_uv_discovery(
    run_python_module: ModuleType, value: str
) -> None:
    """A direct script run outside the action keeps uv's own discovery."""
    assert run_python_module._venv_args({"GC_COVERAGE_PYTHON": value}) == [
        "venv",
        ".venv-coverage",
    ]
