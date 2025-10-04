"""Unit tests covering the validate-linux-packages composite action manifest."""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import yaml
from plumbum import local

from cmd_utils import run_completed_process

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox
else:  # pragma: no cover - typing helper fallback
    CmdMox = typ.Any

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def test_manifest_configures_composite_action() -> None:
    """The action should delegate to the validate.py helper via uv."""
    manifest = yaml.safe_load(ACTION_PATH.read_text())
    assert manifest["runs"]["using"] == "composite"
    steps = manifest["runs"]["steps"]
    assert steps[0]["uses"].startswith("astral-sh/setup-uv@")

    validate_step = steps[1]
    assert validate_step["shell"] == "bash"
    assert 'uv run "${GITHUB_ACTION_PATH}/scripts/validate.py"' in validate_step["run"]

    expected_env = {
        "INPUT_PACKAGE_NAME",
        "INPUT_BIN_NAME",
        "INPUT_TARGET",
        "INPUT_VERSION",
        "INPUT_RELEASE",
        "INPUT_ARCH",
        "INPUT_FORMATS",
        "INPUT_PACKAGES_DIR",
        "INPUT_EXPECTED_PATHS",
        "INPUT_EXECUTABLE_PATHS",
        "INPUT_VERIFY_COMMAND",
        "INPUT_DEB_BASE_IMAGE",
        "INPUT_RPM_BASE_IMAGE",
        "INPUT_POLYTHENE_PATH",
        "INPUT_POLYTHENE_STORE",
        "INPUT_SANDBOX_TIMEOUT",
    }
    assert expected_env.issubset(set(validate_step["env"].keys()))


def test_action_run_step_invokes_validate_script(
    cmd_mox: CmdMox, tmp_path: Path
) -> None:
    """The composite action run script should invoke uv with validate.py."""
    manifest = yaml.safe_load(ACTION_PATH.read_text())
    run_script = manifest["runs"]["steps"][1]["run"]
    action_dir = ACTION_PATH.parent
    validate_script = action_dir / "scripts" / "validate.py"

    shim_dir = cmd_mox.environment.shim_dir
    assert shim_dir is not None

    cmd_mox.stub("uv").with_args("run", validate_script.as_posix()).returns(exit_code=0)
    cmd_mox.replay()

    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_ACTION_PATH"] = str(action_dir)

    command = local["/usr/bin/env"]["bash", "-c", run_script]
    run_completed_process(command, check=True, cwd=tmp_path, env=env)

    cmd_mox.verify()
