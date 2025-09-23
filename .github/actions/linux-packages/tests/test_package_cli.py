"""Unit tests for the package helper CLI behaviours and imports."""

from __future__ import annotations

import importlib
import os
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


def _env_mappings(module: types.ModuleType) -> list[cyclopts.config.Env]:
    """Return the cyclopts environment mapping entries for the module app."""
    return [
        entry
        for entry in getattr(module.app, "config", ())
        if isinstance(entry, cyclopts.config.Env)
    ]


def test_app_config_handles_missing_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Script initialisation tolerates ``App`` implementations without ``config``."""

    class ConfiglessApp:
        def __init__(self) -> None:
            self.registered: list[types.FunctionType] = []

        def default(self, func=None, **kwargs):  # type: ignore[override]
            if func is None:
                def decorator(fn):
                    self.registered.append(fn)
                    return fn

                return decorator
            self.registered.append(func)
            return func

    try:
        with monkeypatch.context() as ctx:
            ctx.setattr(cyclopts, "App", ConfiglessApp)
            module = importlib.reload(pkg_utils.packaging_script)
            env_configs = _env_mappings(module)
            assert len(env_configs) == 1
            assert env_configs[0].prefix == "INPUT_"
    finally:
        importlib.reload(pkg_utils.packaging_script)


def test_app_config_handles_none_initial_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """``App`` instances that zero ``config`` still acquire the env mapping."""

    original_init = cyclopts.App.__init__

    def init_with_none(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        original_init(self, *args, **kwargs)
        self.config = None  # type: ignore[attr-defined]

    try:
        with monkeypatch.context() as ctx:
            ctx.setattr(cyclopts.App, "__init__", init_with_none)
            module = importlib.reload(pkg_utils.packaging_script)
            env_configs = _env_mappings(module)
            assert len(env_configs) == 1
            assert env_configs[0].prefix == "INPUT_"
    finally:
        importlib.reload(pkg_utils.packaging_script)


def test_env_config_appended_once(packaging_module: types.ModuleType) -> None:
    """The cyclopts environment mapping is appended exactly once."""
    env_configs = _env_mappings(packaging_module)
    assert len(env_configs) == 1
    env_cfg = env_configs[0]
    assert env_cfg.prefix == "INPUT_"
    assert env_cfg.command is False

    reloaded = importlib.reload(packaging_module)
    env_configs_reloaded = [
        entry for entry in reloaded.app.config if isinstance(entry, cyclopts.config.Env)
    ]
    assert len(env_configs_reloaded) == 1


def test_normalise_list_preserves_case_variants(
    packaging_module: types.ModuleType,
) -> None:
    """Tokens that differ only by case remain distinct while preserving order."""
    values = ["Foo", "foo", "BAR", "bar", "Mixed", "MIXED"]
    result = packaging_module._normalise_list(values, default=[])
    assert result == ["Foo", "foo", "BAR", "bar", "Mixed", "MIXED"]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("x86_64-unknown-linux-gnu", "amd64"),
        ("i686-unknown-linux-gnu", "i386"),
        ("aarch64-unknown-linux-gnu", "arm64"),
        ("armv7-unknown-linux-gnueabihf", "armhf"),
        ("armv6-unknown-linux-gnueabihf", "armhf"),
        ("armv7l-unknown-linux-gnueabihf", "armhf"),
        ("riscv64gc-unknown-linux-gnu", "riscv64"),
        ("powerpc64le-unknown-linux-gnu", "ppc64el"),
    ],
)
def test_deb_arch_for_target_matches_action_mapping(target: str, expected: str) -> None:
    """Helper mirrors the Debian arch logic used during staging."""
    assert pkg_utils.deb_arch_for_target(target) == expected


def test_deb_arch_for_target_rejects_unknown() -> None:
    """Helper surfaces unknown targets instead of falling back to amd64."""
    with pytest.raises(pkg_utils.packaging_script.UnsupportedTargetError):
        pkg_utils.deb_arch_for_target("mips64-unknown-linux-gnuabi64")


def test_main_errors_for_unknown_target(packaging_module: types.ModuleType) -> None:
    """CLI exits early when the target cannot be mapped to an architecture."""
    with pytest.raises(packaging_module.PackagingError) as exc:
        packaging_module.main(
            bin_name="toy",
            version="1.2.3",
            formats=["deb"],
            target="mips64-unknown-linux-gnuabi64",
        )

    assert "unsupported target triple" in str(exc.value)


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

    monkeypatch.setenv("INPUT_BINARY_DIR", "")
    assert (
        module._coerce_optional_path(
            Path("custom"), "INPUT_BINARY_DIR", fallback=fallback
        )
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
    assert "-f" in command_args
    config_arg = Path(command_args[command_args.index("-f") + 1])
    assert config_arg == Path("dist") / "nfpm.yaml"
    assert "-t" in command_args
    target_arg = Path(command_args[command_args.index("-t") + 1])
    assert target_arg == Path("dist")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    contents = config["contents"]
    assert contents[0]["src"] == bin_path.as_posix()
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
    missing_geteuid = False
    if not hasattr(os, "geteuid"):
        missing_geteuid = True
        os.geteuid = lambda: 0  # type: ignore[attr-defined]
    try:
        result_globals = runpy.run_path(module_path.as_posix(), run_name=module_name)
        helper_module = sys.modules.get("script_utils")
    finally:
        if missing_geteuid:
            delattr(os, "geteuid")
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
