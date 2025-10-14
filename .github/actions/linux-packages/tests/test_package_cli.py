"""Unit tests for the package helper CLI behaviours and imports."""

from __future__ import annotations

import importlib
import os
import runpy
import shutil
import stat
import sys
import tempfile
import types
import typing as typ
from collections import defaultdict
from pathlib import Path

import _packaging_utils as pkg_utils
import cyclopts
import pytest
import yaml
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd


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


class _FakeBoundCommand:
    """Minimal plumbum-like command proxy for unit tests."""

    def __init__(self, name: str, args: tuple[object, ...] = ()) -> None:
        self._name = name
        self._args = tuple(args)

    def __getitem__(self, args: object) -> _FakeBoundCommand:
        if not isinstance(args, tuple):
            args = (args,)
        return _FakeBoundCommand(self._name, self._args + tuple(args))

    def formulate(self) -> list[str]:
        result = [self._name]
        for arg in self._args:
            if isinstance(arg, Path):
                result.append(arg.as_posix())
            else:
                result.append(str(arg))
        return result


class _FakeLocal:
    """Lightweight shim emulating ``plumbum.local`` for unit tests."""

    def __getitem__(self, name: str) -> _FakeBoundCommand:
        return _FakeBoundCommand(name)


def _make_fake_run_cmd(
    *, checksum_text: str | None = None, fail_checksums: bool = False
) -> typ.Callable[..., str]:
    """Return a ``run_cmd`` stub that emulates the nfpm download flow."""

    def fake_run_cmd(cmd: _FakeBoundCommand, *args: object, **kwargs: object) -> str:
        argv = [str(part) for part in cmd.formulate()]
        name = Path(argv[0]).name
        if name == "uname":
            if "-m" in argv:
                return "x86_64"
            if "-s" in argv:
                return "Linux"
            pytest.fail(f"unexpected uname invocation: {argv}")
        if name == "curl":
            output_idx = argv.index("-o") + 1
            output_path = Path(argv[output_idx])
            if output_path.name.endswith("_checksums.txt"):
                if fail_checksums:
                    raise ProcessExecutionError(argv, 1, "", "not found")
                output_path.write_text(checksum_text or "", encoding="utf-8")
                return ""
            output_path.write_bytes(b"dummy nfpm archive")
            return ""
        if name in {"tar", "install"}:
            return ""
        pytest.fail(f"unexpected command invocation: {argv}")

    return fake_run_cmd


def test_app_config_handles_missing_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Script initialisation tolerates ``App`` implementations without ``config``."""

    class ConfiglessApp:
        def __init__(self) -> None:
            self.registered: list[types.FunctionType] = []

        def default(
            self,
            func: types.FunctionType | None = None,
            **kwargs: object,
        ) -> types.FunctionType:  # type: ignore[override]
            if func is None:

                def decorator(fn: types.FunctionType) -> types.FunctionType:
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

    def init_with_none(self: cyclopts.App, *args: object, **kwargs: object) -> None:
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


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix permissions not supported on Windows"
)
def test_ensure_executable_permissions_sets_exec_bits(
    packaging_module: types.ModuleType, tmp_path: Path
) -> None:
    """Helper restores execute permissions without clobbering other bits."""
    binary = tmp_path / "bin"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o640)

    mode = packaging_module._ensure_executable_permissions(binary)

    assert mode is not None
    assert stat.S_IMODE(mode) == 0o751
    resulting = binary.stat().st_mode & 0o777
    assert resulting & stat.S_IXUSR
    assert resulting & stat.S_IXGRP
    assert resulting & stat.S_IXOTH
    assert resulting & stat.S_IRUSR
    assert resulting & stat.S_IWUSR


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix permissions not supported on Windows"
)
def test_ensure_executable_permissions_returns_existing_mode(
    packaging_module: types.ModuleType, tmp_path: Path
) -> None:
    """Helper returns the original mode when execute bits are already set."""
    binary = tmp_path / "bin"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o755)

    original_mode = binary.stat().st_mode
    result = packaging_module._ensure_executable_permissions(binary)

    assert result == original_mode
    assert binary.stat().st_mode == original_mode


def test_ensure_executable_permissions_skips_on_windows(
    packaging_module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows platforms skip chmod adjustments to avoid unsupported operations."""
    binary = tmp_path / "bin"
    binary.write_bytes(b"echo")
    binary.chmod(0o640)
    original_mode = binary.stat().st_mode

    monkeypatch.setattr(packaging_module, "os", types.SimpleNamespace(name="nt"))

    result = packaging_module._ensure_executable_permissions(binary)

    assert result is None
    assert binary.stat().st_mode == original_mode


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix permissions not supported on Windows"
)
def test_ensure_executable_permissions_returns_none_when_stat_unavailable(
    packaging_module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stat failures are surfaced by returning ``None`` without chmod attempts."""
    binary = tmp_path / "bin"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o640)

    original_stat = packaging_module.Path.stat
    original_chmod = packaging_module.Path.chmod

    def failing_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:  # type: ignore[override]
        if self == binary:
            message = "stat unavailable"
            raise OSError(message)
        return original_stat(self, follow_symlinks=follow_symlinks)

    def failing_chmod(self: Path, mode: int) -> None:  # type: ignore[override]
        if self == binary:
            pytest.fail("chmod should not be attempted when stat fails")
        original_chmod(self, mode)

    monkeypatch.setattr(packaging_module.Path, "stat", failing_stat)
    monkeypatch.setattr(packaging_module.Path, "chmod", failing_chmod)

    result = packaging_module._ensure_executable_permissions(binary)

    assert result is None


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


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix permissions not supported on Windows"
)
def test_main_reinstates_binary_execute_permissions(
    packaging_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI normalises binary modes before invoking nfpm."""
    target = "x86_64-unknown-linux-gnu"
    release_dir = tmp_path / "target" / target / "release"
    release_dir.mkdir(parents=True)
    binary = release_dir / "toy"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o640)

    outdir = tmp_path / "dist"
    config_out = tmp_path / "nfpm.yaml"

    monkeypatch.setattr(
        packaging_module, "get_command", lambda _: _FakeBoundCommand("nfpm")
    )
    monkeypatch.setattr(packaging_module, "run_cmd", lambda *_, **__: None)

    packaging_module.main(
        bin_name="toy",
        version="1.2.3",
        formats=["deb"],
        target=target,
        binary_dir=tmp_path / "target",
        outdir=outdir,
        config_out=config_out,
    )

    mode = binary.stat().st_mode & 0o777
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH


@pytest.mark.skipif(
    sys.platform == "win32", reason="Unix permissions not supported on Windows"
)
def test_main_uses_cached_mode_from_permission_helper(
    packaging_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``main`` reuses the mode returned by the helper instead of re-statting."""
    target = "x86_64-unknown-linux-gnu"
    release_dir = tmp_path / "target" / target / "release"
    release_dir.mkdir(parents=True)
    binary = release_dir / "toy"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o640)

    outdir = tmp_path / "dist"
    config_out = tmp_path / "nfpm.yaml"

    monkeypatch.setattr(
        packaging_module, "get_command", lambda _: _FakeBoundCommand("nfpm")
    )
    monkeypatch.setattr(packaging_module, "run_cmd", lambda *_, **__: None)
    monkeypatch.setattr(packaging_module, "ensure_exists", lambda *_: None)

    original_stat = packaging_module.Path.stat
    call_counts: dict[Path, int] = defaultdict(int)

    def monitored_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:  # type: ignore[override]
        call_counts[self] += 1
        return original_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(packaging_module.Path, "stat", monitored_stat)

    packaging_module.main(
        bin_name="toy",
        version="1.2.3",
        formats=["deb"],
        target=target,
        binary_dir=tmp_path / "target",
        outdir=outdir,
        config_out=config_out,
    )

    assert call_counts[binary] == 1


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


def test_ensure_nfpm_raises_when_checksum_download_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Checksum download failures abort nfpm provisioning."""
    monkeypatch.delenv("NFPM_VERSION", raising=False)
    monkeypatch.setattr(pkg_utils.shutil, "which", lambda _: None)
    monkeypatch.setattr(pkg_utils, "local", _FakeLocal())
    monkeypatch.setattr(
        pkg_utils,
        "run_cmd",
        _make_fake_run_cmd(fail_checksums=True),
    )
    checks_url = (
        "https://github.com/goreleaser/nfpm/releases/download/"
        "v2.39.0/nfpm_2.39.0_checksums.txt"
    )

    with pytest.raises(RuntimeError) as excinfo, pkg_utils.ensure_nfpm(tmp_path):
        pass

    assert checks_url in str(excinfo.value)


def test_ensure_nfpm_errors_when_checksum_entry_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing checksum entries surface as runtime errors."""
    monkeypatch.delenv("NFPM_VERSION", raising=False)
    monkeypatch.setattr(pkg_utils.shutil, "which", lambda _: None)
    monkeypatch.setattr(pkg_utils, "local", _FakeLocal())
    monkeypatch.setattr(
        pkg_utils,
        "run_cmd",
        _make_fake_run_cmd(checksum_text="deadbeef  other_asset.tar.gz\n"),
    )

    with pytest.raises(RuntimeError) as excinfo, pkg_utils.ensure_nfpm(tmp_path):
        pass

    message = str(excinfo.value)
    assert "missing entry" in message
    assert "nfpm_2.39.0_Linux_x86_64.tar.gz" in message


@pytest.mark.usefixtures("uncapture_if_verbose")
@pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("dpkg-deb") is None
    or not pkg_utils.HAS_PODMAN_RUNTIME
    or shutil.which("uv") is None,
    reason="dpkg-deb, podman runtime or uv not available",
)
def test_package_cli_stages_binary_with_executable_permissions(
    packaging_project_paths: pkg_utils.PackagingProject,
    build_artifacts: pkg_utils.BuildArtifacts,
    packaging_config: pkg_utils.PackagingConfig,
) -> None:
    """The CLI normalises the staged binary to be executable before packaging."""
    bin_path = (
        packaging_project_paths.project_dir
        / "target"
        / build_artifacts.target
        / "release"
        / packaging_config.bin_name
    )
    bin_path.chmod(0o644)
    assert bin_path.stat().st_mode & 0o777 == 0o644

    packages = pkg_utils.package_project(
        packaging_project_paths,
        build_artifacts,
        config=packaging_config,
        formats=("deb",),
    )
    deb_path = packages.get("deb")
    assert deb_path is not None, "expected deb package to be produced"

    with tempfile.TemporaryDirectory() as td:
        run_cmd(local["dpkg-deb"]["-x", str(deb_path), td])
        extracted = Path(td, "usr/bin", packaging_config.bin_name)
        assert extracted.is_file()
        mode = extracted.stat().st_mode & 0o777
        assert mode == 0o755, f"expected 0o755 permissions but found {oct(mode)}"


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
