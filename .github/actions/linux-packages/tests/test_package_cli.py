"""Unit tests for the package helper CLI behaviours and imports."""

from __future__ import annotations

import importlib
import runpy
import sys
import types
from pathlib import Path

import _packaging_utils as pkg_utils
import cyclopts
import pytest
import yaml


@pytest.fixture
def packaging_module() -> types.ModuleType:
    """Reload the packaging script to provide a clean module instance."""
    return importlib.reload(pkg_utils.packaging_script)


def test_env_config_appended_once(packaging_module: types.ModuleType) -> None:
    """The cyclopts environment mapping is appended exactly once."""
    env_configs = [
        entry
        for entry in packaging_module.app.config
        if isinstance(entry, cyclopts.config.Env)
    ]
    assert len(env_configs) == 1
    env_cfg = env_configs[0]
    assert env_cfg.prefix == "INPUT_"
    assert env_cfg.command is False

    reloaded = importlib.reload(packaging_module)
    env_configs_reloaded = [
        entry for entry in reloaded.app.config if isinstance(entry, cyclopts.config.Env)
    ]
    assert len(env_configs_reloaded) == 1


def test_normalise_list_dedupes_casefold(packaging_module: types.ModuleType) -> None:
    """Tokens differing only by case are deduplicated while preserving order."""
    values = ["Foo", "foo", "BAR", "bar", "Mixed", "MIXED"]
    result = packaging_module._normalise_list(values, default=[])
    assert result == ["Foo", "BAR", "Mixed"]


def test_coerce_optional_path_handles_none_and_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None values and blank environment overrides fall back to the provided default."""
    module = importlib.reload(pkg_utils.packaging_script)
    fallback = Path("target")
    monkeypatch.delenv("INPUT_BINARY_DIR", raising=False)
    assert (
        module._coerce_optional_path(None, "INPUT_BINARY_DIR", fallback=fallback)
        == fallback
    )

    monkeypatch.setenv("INPUT_BINARY_DIR", "   ")
    assert (
        module._coerce_optional_path(Path("  "), "INPUT_BINARY_DIR", fallback=fallback)
        == fallback
    )

    expected = Path("custom")
    monkeypatch.setenv("INPUT_BINARY_DIR", "custom")
    assert (
        module._coerce_optional_path(expected, "INPUT_BINARY_DIR", fallback=fallback)
        == expected
    )


def test_main_uses_default_paths_for_blank_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank inputs fall back to default directories for all derived paths."""
    module = importlib.reload(pkg_utils.packaging_script)
    target = "x86_64-unknown-linux-gnu"
    bin_name = "toy"

    monkeypatch.chdir(tmp_path)
    for name in (
        "INPUT_BINARY_DIR",
        "INPUT_OUTDIR",
        "INPUT_CONFIG_PATH",
        "INPUT_MAN_STAGE",
    ):
        monkeypatch.setenv(name, "   ")

    bin_path = Path("target") / target / "release" / bin_name
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\n", encoding="utf-8")

    man_dir = Path("docs")
    man_dir.mkdir(parents=True, exist_ok=True)
    man_source = man_dir / f"{bin_name}.1"
    man_source.write_text(".TH toy 1\n", encoding="utf-8")

    commands: list[list[str]] = []

    class FakeBoundCommand:
        def __init__(self, args: tuple[str, ...]) -> None:
            self._args = list(args)

        def formulate(self) -> list[str]:
            return list(self._args)

    class FakeCommand:
        def __getitem__(self, args: tuple[str, ...]) -> FakeBoundCommand:
            return FakeBoundCommand(args)

    fake_nfpm = FakeCommand()
    monkeypatch.setattr(module, "get_command", lambda name: fake_nfpm)
    monkeypatch.setattr(module, "run_cmd", lambda cmd: commands.append(cmd.formulate()))

    module.main(
        bin_name=bin_name,
        version="1.2.3",
        formats=["deb"],
        target=target,
        man_paths=[man_source],
    )

    dist_dir = tmp_path / "dist"
    config_path = dist_dir / "nfpm.yaml"
    man_stage = dist_dir / ".man"

    assert config_path.is_file()
    assert man_stage.is_dir()
    assert commands, "nfpm command should be invoked"
    command_args = commands[0]
    assert "dist/nfpm.yaml" in command_args
    assert "-f" in command_args
    assert command_args[command_args.index("-f") + 1] == "dist/nfpm.yaml"
    assert "-t" in command_args
    assert command_args[command_args.index("-t") + 1] == "dist"

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    contents = config["contents"]
    assert contents[0]["src"] == str(bin_path)
    staged_files = list(man_stage.glob("*.gz"))
    assert staged_files, "expected gzipped manpage in fallback stage directory"


def _run_script_with_fallback(
    script: str, module_name: str
) -> tuple[object, types.ModuleType | None]:
    """Execute ``script`` via runpy using the ImportError fallback path."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    module_path = scripts_dir / script
    original_sys_path = list(sys.path)
    original_helper = sys.modules.get("script_utils")
    try:
        result_globals = runpy.run_path(module_path.as_posix(), run_name=module_name)
        helper_module = sys.modules.get("script_utils")
    finally:
        sys.path[:] = original_sys_path
        if original_helper is None:
            sys.modules.pop("script_utils", None)
        else:
            sys.modules["script_utils"] = original_helper
    module_obj = sys.modules.pop(module_name, None)
    if module_obj is None:
        module_obj = types.SimpleNamespace(**result_globals)
    return module_obj, helper_module


def test_package_import_fallback() -> None:
    """When executed as a script, package.py loads helpers via sys.path fallback."""
    module_obj, helper_module = _run_script_with_fallback(
        "package.py", "package_fallback_test"
    )
    assert helper_module is not None
    assert module_obj.ensure_directory is helper_module.ensure_directory
    assert module_obj.run_cmd is helper_module.run_cmd


def test_polythene_import_fallback() -> None:
    """When executed as a script, polythene.py uses the helper fallback."""
    module_obj, helper_module = _run_script_with_fallback(
        "polythene.py", "polythene_fallback_test"
    )
    assert helper_module is not None
    assert module_obj.ensure_directory is helper_module.ensure_directory
    assert module_obj.run_cmd is helper_module.run_cmd


def test_script_utils_imports_cmd_utils(tmp_path: Path) -> None:
    """The script_utils fallback loads the repository-level cmd_utils module."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    module_path = scripts_dir / "script_utils.py"
    module_name = "script_utils_fallback_test"

    existing_cmd_utils = sys.modules.get("cmd_utils")
    result_globals = runpy.run_path(module_path.as_posix(), run_name=module_name)
    module_obj = sys.modules.get(module_name)

    import cmd_utils as repo_cmd_utils

    run_cmd_fn = (
        module_obj.run_cmd if module_obj is not None else result_globals["run_cmd"]
    )
    assert run_cmd_fn is repo_cmd_utils.run_cmd

    if existing_cmd_utils is not None:
        sys.modules["cmd_utils"] = existing_cmd_utils
    sys.modules.pop("script_utils", None)
    sys.modules.pop(module_name, None)
