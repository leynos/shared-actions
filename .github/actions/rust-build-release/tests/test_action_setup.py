"""Tests for composite action setup helpers."""

from __future__ import annotations

import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from typer.testing import CliRunner

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from types import ModuleType

    from .conftest import HarnessFactory

runner = CliRunner()

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "src" / "action_setup.py"


def test_calculate_insertion_index_empty_path(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ``sys.path`` is empty the repo root is inserted at index ``0``."""
    monkeypatch.setattr(action_setup_module.sys, "path", [])
    assert action_setup_module._calculate_insertion_index(tmp_path) == 0


def test_calculate_insertion_index_preserves_script_dir_prefix(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Leading script directories move the repo root insertion point to ``1``."""
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(
        action_setup_module.sys, "path", [script_dir.as_posix(), "other"]
    )
    assert action_setup_module._calculate_insertion_index(script_dir) == 1


def test_calculate_insertion_index_handles_blank_entries(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank leading entries do not offset the insertion index."""
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr(action_setup_module.sys, "path", ["", "other"])
    assert action_setup_module._calculate_insertion_index(script_dir) == 0


def test_validate_target_accepts_valid(action_setup_module: ModuleType) -> None:
    """Valid targets pass validation."""
    action_setup_module.validate_target("x86_64-unknown-linux-gnu")


@pytest.mark.parametrize(
    "target",
    ["", "invalid target", "short"],
)
def test_validate_target_rejects_invalid(
    target: str, action_setup_module: ModuleType
) -> None:
    """Invalid targets raise TargetValidationError."""
    with pytest.raises(action_setup_module.TargetValidationError):
        action_setup_module.validate_target(target)


def test_resolve_toolchain_windows_unknown_arch(
    action_setup_module: ModuleType,
) -> None:
    """Unknown architectures raise ToolchainResolutionError."""
    with pytest.raises(action_setup_module.ToolchainResolutionError):
        action_setup_module.resolve_toolchain(
            "1.89.0", "x86_64-pc-windows-gnu", "Windows", "SPARC"
        )


def test_resolve_toolchain_windows_known_arch(
    action_setup_module: ModuleType,
) -> None:
    """Known Windows architectures return the GNU toolchain triple."""
    resolved = action_setup_module.resolve_toolchain(
        "1.89.0", "aarch64-pc-windows-gnu", "Windows", "ARM64"
    )
    assert resolved == "1.89.0-aarch64-pc-windows-gnu"


def test_cli_toolchain_outputs_value(
    action_setup_module: ModuleType,
    toolchain_module: ModuleType,
    module_harness: HarnessFactory,
    tmp_path: Path,
) -> None:
    """CLI command emits the resolved toolchain."""
    harness = module_harness(toolchain_module)
    custom_file = tmp_path / "TOOLCHAIN_VERSION"
    custom_file.write_text("1.99.0\n", encoding="utf-8")
    harness.monkeypatch.setattr(toolchain_module, "TOOLCHAIN_VERSION_FILE", custom_file)
    harness.monkeypatch.setattr(
        action_setup_module,
        "read_default_toolchain",
        toolchain_module.read_default_toolchain,
    )
    result = runner.invoke(
        action_setup_module.app,
        [
            "toolchain",
            "--target",
            "x86_64-unknown-linux-gnu",
            "--runner-os",
            "Linux",
            "--runner-arch",
            "X64",
        ],
        prog_name="action-setup",
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "1.99.0"


def test_cli_validate_emits_error(action_setup_module: ModuleType) -> None:
    """CLI validation command reports errors via Typer exit codes."""
    result = runner.invoke(
        action_setup_module.app,
        ["validate", "invalid target"],
        prog_name="action-setup",
    )
    assert result.exit_code != 0
    assert "contains invalid characters" in result.stderr


def test_script_validate_step_reports_error() -> None:
    """Running the script like the composite action reports invalid targets."""
    command = local[sys.executable][str(SCRIPT_PATH), "validate", "short"]
    result = run_plumbum_command(command, method="run")
    assert result.returncode != 0
    combined = f"{result.stdout}\n{result.stderr}"
    assert "must contain at least two '-' separated segments" in combined


def test_script_toolchain_step_resolves_windows(toolchain_module: ModuleType) -> None:
    """Script execution mirrors the composite action's toolchain resolution."""
    default = toolchain_module.read_default_toolchain()
    command = local[sys.executable][
        str(SCRIPT_PATH),
        "toolchain",
        "--target",
        "aarch64-pc-windows-gnu",
        "--runner-os",
        "Windows",
        "--runner-arch",
        "ARM64",
    ]
    result = run_plumbum_command(command, method="run")
    assert result.returncode == 0
    assert result.stdout.strip() == f"{default}-aarch64-pc-windows-gnu"
