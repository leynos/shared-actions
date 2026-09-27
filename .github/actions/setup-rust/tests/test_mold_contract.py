"""Contracts for how setup-rust wires its pinned mold installer.

The installer's behaviour is tested in ``test_install_mold.py``. These tests
hold the manifest to the shape that makes that behaviour reachable: the install
step runs only on Linux and only when asked, runs the script as its sole
command, and the off-Linux arm is a notice rather than a failure. They also
hold the action to setting no linker flag, which is the consumer's choice.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    from types import ModuleType

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_mold.py"

#: The install step's whole command, as the folded scalar joins it.
INSTALL_COMMAND = (
    'uv run --script "$SR_MOLD_SCRIPT" --mold-version "$SR_MOLD_VERSION" '
    '--runner-arch "$SR_RUNNER_ARCH" --tool-cache "$SR_TOOL_CACHE" '
    '--temp-dir "$SR_RUNNER_TEMP" --github-path "$GITHUB_PATH" '
    '--github-output "$GITHUB_OUTPUT"'
)


def _load_installer() -> ModuleType:
    """Import the installer script by path; the action's scripts are no package."""
    spec = importlib.util.spec_from_file_location("install_mold_contract", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed setup-rust manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _step(step_id: str) -> dict[str, typ.Any]:
    """Return the step with *step_id*, failing clearly when it is absent."""
    steps = [step for step in _manifest()["runs"]["steps"] if step.get("id") == step_id]
    assert len(steps) == 1, f"expected one step with id {step_id!r}"
    return steps[0]


@pytest.mark.parametrize(
    ("step_id", "guard"),
    [
        pytest.param(
            "mold",
            "${{ inputs.install-mold == 'true' && runner.os == 'Linux' }}",
            id="install",
        ),
        pytest.param(
            "mold-skip",
            "${{ inputs.install-mold == 'true' && runner.os != 'Linux' }}",
            id="skip",
        ),
    ],
)
def test_each_mold_arm_runs_only_when_asked_on_its_platform(
    step_id: str, guard: str
) -> None:
    """The two arms partition the runners, and neither runs unless asked.

    Compared whole: a guard with an extra ``|| ...`` or a dropped term
    would still contain either term.
    """
    assert " ".join(str(_step(step_id)["if"]).split()) == guard


def test_the_install_step_runs_the_script_as_its_sole_command() -> None:
    """``false && <command>`` contains the command and runs nothing."""
    step = _step("mold")

    assert " ".join(str(step["run"]).split()) == INSTALL_COMMAND
    assert step["env"]["SR_MOLD_VERSION"] == "${{ inputs.mold-version }}"
    assert step["env"]["SR_MOLD_SCRIPT"] == (
        "${{ github.action_path }}/scripts/install_mold.py"
    )


def test_the_off_linux_arm_notices_and_never_fails() -> None:
    """A matrix passes ``install-mold`` unconditionally, so macOS and Windows skip."""
    run = str(_step("mold-skip")["run"])

    assert run.splitlines()[0].startswith('echo "::notice title=setup-rust mold::')
    assert 'echo "status=skipped" >> "$GITHUB_OUTPUT"' in run.splitlines()
    assert "exit" not in run


def test_the_outputs_report_both_arms() -> None:
    """``mold-status`` reads whichever arm ran; ``mold-version`` only the install."""
    outputs = _manifest()["outputs"]

    assert outputs["mold-status"]["value"] == (
        "${{ steps.mold.outputs.status || steps.mold-skip.outputs.status }}"
    )
    assert outputs["mold-version"]["value"] == "${{ steps.mold.outputs.version }}"


def test_the_default_version_is_pinned_for_every_supported_architecture() -> None:
    """A caller relying on the default must never meet an unpinned archive."""
    installer = _load_installer()
    inputs = _manifest()["inputs"]

    assert inputs["install-mold"]["default"] == "false"
    default = inputs["mold-version"]["default"]
    for arch in installer.RUNNER_ARCHES.values():
        assert (default, arch) in installer.MOLD_DIGESTS, (default, arch)


def test_the_action_sets_no_linker_flag() -> None:
    """Selecting mold belongs to the consumer's ``.cargo/config.toml``.

    Checked over every step's command and environment, so neither a
    ``-fuse-ld`` flag nor a ``CARGO_TARGET_*_LINKER`` variable can appear.
    """
    offenders = [
        step.get("name")
        for step in _manifest()["runs"]["steps"]
        if "fuse-ld" in f"{step.get('run', '')}{step.get('env', '')}"
        or "_LINKER" in f"{step.get('run', '')}{step.get('env', '')}"
    ]

    assert offenders == []
