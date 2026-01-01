"""Tests for composite action setup helpers."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import typing as typ
import uuid
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
TOOLCHAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "toolchain.py"


def _load_action_setup_from_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, add_root_marker: bool
) -> tuple[ModuleType, Path, Path]:
    """Load action_setup.py from a temporary repository layout."""
    repo_root = tmp_path / "repo"
    action_dir = repo_root / ".github" / "actions" / "rust-build-release"
    src_dir = action_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(SCRIPT_PATH, src_dir / "action_setup.py")
    shutil.copy(TOOLCHAIN_PATH, src_dir / "toolchain.py")
    (repo_root / "cmd_utils_importer.py").write_text(
        "def ensure_cmd_utils_imported():\n    return None\n",
        encoding="utf-8",
    )
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "dummy"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (action_dir / "action.yml").write_text(
        "name: Rust Build Release\nruns:\n  using: composite\n  steps: []\n",
        encoding="utf-8",
    )
    if add_root_marker:
        (repo_root / "action.yml").write_text(
            "name: Root Action\nruns:\n  using: composite\n  steps: []\n",
            encoding="utf-8",
        )

    monkeypatch.delenv("GITHUB_ACTION_PATH", raising=False)
    module_name = f"rbr_action_setup_{uuid.uuid4().hex}"
    module_path = src_dir / "action_setup.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - import guard
        message = f"Failed to load {module_path}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "path", [str(src_dir), *sys.path])
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[misc]
    return module, repo_root, action_dir


def _reset_bootstrap_cache(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear cached bootstrap data for ``module`` within a test."""
    monkeypatch.setattr(module, "_BOOTSTRAP_CACHE", None, raising=False)


def test_bootstrap_inserts_repo_root_first_when_path_empty(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty ``sys.path`` receives the repo root once."""
    path_entries: list[str] = []
    monkeypatch.setattr(sys, "path", path_entries)
    _reset_bootstrap_cache(action_setup_module, monkeypatch)

    _, repo_root = action_setup_module.bootstrap_environment()

    assert path_entries == [str(repo_root)]

    repo_root_str = str(repo_root)
    _reset_bootstrap_cache(action_setup_module, monkeypatch)
    action_setup_module.bootstrap_environment()

    assert len([entry for entry in path_entries if entry == repo_root_str]) == 1


def test_bootstrap_inserts_repo_root_after_script_dir_prefix(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo root insertion preserves existing path prefix entries."""
    script_dir = Path(action_setup_module.__file__).resolve().parent
    script_dir_str = str(script_dir)
    path_entries: list[str] = [script_dir_str, "other"]
    monkeypatch.setattr(sys, "path", path_entries)
    _reset_bootstrap_cache(action_setup_module, monkeypatch)

    _, repo_root = action_setup_module.bootstrap_environment()

    assert path_entries == [str(repo_root), script_dir_str, "other"]


def test_bootstrap_ignores_blank_first_entry_for_insertion_index(
    action_setup_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank ``sys.path`` entries remain while repo root is prepended."""
    path_entries = ["", "other"]
    monkeypatch.setattr(sys, "path", path_entries)
    sentinel = "sentinel-action-path"
    monkeypatch.setenv("GITHUB_ACTION_PATH", sentinel)
    _reset_bootstrap_cache(action_setup_module, monkeypatch)

    _, repo_root = action_setup_module.bootstrap_environment()

    assert path_entries[0] == str(repo_root)
    assert path_entries.count(str(repo_root)) == 1
    assert "" in path_entries
    assert path_entries.index("") == 1
    assert os.environ["GITHUB_ACTION_PATH"] == sentinel


@pytest.mark.parametrize(
    "initial_paths",
    [
        ["other", "<repo_root>", "another"],
        ["first", "second", "<repo_root>"],
        ["<repo_root>", "other", "<repo_root>"],
    ],
    ids=["repo_root_middle", "repo_root_end", "repo_root_multiple"],
)
def test_bootstrap_dedupes_existing_repo_root(
    action_setup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    initial_paths: list[str],
) -> None:
    """Existing repo root entries are deduplicated and moved to the front."""
    _reset_bootstrap_cache(action_setup_module, monkeypatch)
    _, repo_root = action_setup_module.bootstrap_environment()
    repo_root_str = str(repo_root)

    resolved_paths = [
        repo_root_str if entry == "<repo_root>" else entry for entry in initial_paths
    ]
    monkeypatch.setattr(sys, "path", list(resolved_paths))
    _reset_bootstrap_cache(action_setup_module, monkeypatch)

    action_setup_module.bootstrap_environment()

    assert sys.path[0] == repo_root_str
    assert sys.path.count(repo_root_str) == 1
    expected_tail = [entry for entry in resolved_paths if entry != repo_root_str]
    assert sys.path[1:] == expected_tail


def test_bootstrap_discovers_action_and_repo_root_from_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap finds action.yml and pyproject.toml markers in custom layouts."""
    module, repo_root, action_dir = _load_action_setup_from_layout(
        tmp_path, monkeypatch, add_root_marker=False
    )

    action_path, repo_root_found = module.bootstrap_environment()

    assert action_path == action_dir
    assert repo_root_found == repo_root
    assert action_dir == module._ACTION_PATH
    assert repo_root == module._REPO_ROOT
    assert os.environ["GITHUB_ACTION_PATH"] == str(action_dir)


def test_bootstrap_prefers_nearest_action_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nearest action.yml wins when multiple markers exist."""
    module, repo_root, action_dir = _load_action_setup_from_layout(
        tmp_path, monkeypatch, add_root_marker=True
    )

    assert action_dir == module._ACTION_PATH
    assert repo_root == module._REPO_ROOT
    assert os.environ["GITHUB_ACTION_PATH"] == str(action_dir)


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
