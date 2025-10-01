"""Unit tests covering the validate-linux-packages composite action manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

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
        "INPUT_PACKAGE_DIR",
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
