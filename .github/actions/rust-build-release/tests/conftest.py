"""Shared fixtures and helpers for rust-build-release tests."""

from __future__ import annotations

import collections.abc as cabc
import importlib
import importlib.util
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from syspath_hack import prepend_to_syspath

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd

if typ.TYPE_CHECKING:
    from cmd_utils import SupportsFormulate
else:  # pragma: no cover - typing helper fallback
    SupportsFormulate = typ.Any

try:
    from ._packaging_utils import (
        DEFAULT_CONFIG as _DEFAULT_CONFIG,
    )
    from ._packaging_utils import (
        DEFAULT_TARGET as _DEFAULT_TARGET,
    )
    from ._packaging_utils import (
        BuildArtefacts as _BuildArtefacts,
    )
    from ._packaging_utils import (
        PackagingConfig as _PackagingConfig,
    )
    from ._packaging_utils import (
        PackagingProject as _PackagingProject,
    )
    from ._packaging_utils import (
        build_release_artefacts as _build_release_artefacts,
    )
    from ._packaging_utils import (
        clone_packaging_project as _clone_packaging_project,
    )
    from ._packaging_utils import (
        package_project as _package_project,
    )
    from ._packaging_utils import (
        packaging_project as _packaging_project,
    )
except Exception:  # noqa: BLE001
    _NO_SPEC_MSG = "failed to import packaging utils: spec not found"
    pkg_utils_path = (
        Path(__file__).resolve().parents[2]
        / "linux-packages"
        / "tests"
        / "_packaging_utils.py"
    )
    spec = importlib.util.spec_from_file_location(
        "linux_packages_packaging_utils", pkg_utils_path
    )
    if spec is None or spec.loader is None:
        raise SystemExit(_NO_SPEC_MSG) from None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _DEFAULT_CONFIG = mod.DEFAULT_CONFIG
    _DEFAULT_TARGET = mod.DEFAULT_TARGET
    _BuildArtefacts = mod.BuildArtefacts
    _PackagingConfig = mod.PackagingConfig
    _PackagingProject = mod.PackagingProject
    _clone_packaging_project = mod.clone_packaging_project
    _build_release_artefacts = mod.build_release_artefacts
    _package_project = mod.package_project
    _packaging_project = mod.packaging_project

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

if typ.TYPE_CHECKING:
    import subprocess
    import types as types_module

    ModuleType = types_module.ModuleType
else:  # pragma: no cover - type checking only
    ModuleType = typ.Any


IteratorNone = typ.Iterator[None]

WINDOWS_SMOKE_TEST = "test_action_builds_release_binary_and_manpage"
WINDOWS_XFAIL_REASON = (
    "Known failure on Windows; see https://github.com/leynos/shared-actions/issues/93"
)


@pytest.fixture(autouse=True)
def isolated_rust_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide writable cargo/rustup homes for integration tests."""
    rustup_bin = shutil.which("rustup")
    if rustup_bin is None:  # pragma: no cover - guarded by caller checks
        pytest.skip("rustup not installed")
    if not Path(rustup_bin).is_file():
        pytest.skip(f"rustup missing at resolved path: {rustup_bin}")

    # Determine the actual cargo home from environment or use default
    import os

    cargo_home_str = os.environ.get("CARGO_HOME")
    cargo_home = Path(cargo_home_str) if cargo_home_str else Path.home() / ".cargo"

    # Keep rustup state isolated, but use the real cargo home so the rustup
    # shim continues to find its installed path layout.
    rustup_home = tmp_path / "rustup-home"
    rustup_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    monkeypatch.setenv("RUSTUP_HOME", str(rustup_home))


def _ensure_dependency(name: str, attribute: str | None = None) -> None:
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError:  # pragma: no cover - environment guard
        pytest.skip(f"{name} not installed")
    if attribute and not hasattr(module, attribute):
        pytest.skip(f"{name} not installed")


def _load_module(
    filename: str,
    module_name: str,
    *,
    deps: cabc.Sequence[tuple[str, str | None]] = (),
) -> ModuleType:
    prepend_to_syspath(SRC_DIR)
    for dep_name, attr in deps:
        _ensure_dependency(dep_name, attr)
    module_path = SRC_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        msg = f"failed to load module {module_name} from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModuleHarness:
    """Utility wrapper around a loaded module for patching helpers."""

    def __init__(self, module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.module = module
        self.monkeypatch = monkeypatch
        self.calls: list[list[str]] = []

    def patch_run_cmd(
        self,
        side_effect: cabc.Callable[[list[str]], object | None] | None = None,
    ) -> None:
        """Patch ``run_cmd`` to record calls and execute an optional side effect."""

        def fake(cmd: SupportsFormulate) -> object | None:
            formulated = list(cmd.formulate())
            if formulated:
                formulated[0] = Path(formulated[0]).name
            self.calls.append(formulated)
            return side_effect(formulated) if side_effect is not None else None

        self.monkeypatch.setattr(self.module, "run_cmd", fake)

    def patch_shutil_which(self, func: cabc.Callable[[str], str | None]) -> None:
        """Patch ``shutil.which`` for the wrapped module."""
        self.monkeypatch.setattr(self.module.shutil, "which", func)

    def patch_platform(self, platform: str) -> None:
        """Force ``sys.platform`` to ``platform`` within the module."""
        self.monkeypatch.setattr(self.module.sys, "platform", platform)

    def patch_attr(self, name: str, value: object) -> None:
        """Patch an arbitrary attribute on the wrapped module."""
        self.monkeypatch.setattr(self.module, name, value)

    def patch_subprocess_run(
        self,
        func: cabc.Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        """Patch ``run_validated`` to use *func* within the wrapped module."""
        self.monkeypatch.setattr(self.module, "run_validated", func)


HarnessFactory = cabc.Callable[[ModuleType], ModuleHarness]


class _DummyCommand:
    def __init__(self, name: str = "dummy") -> None:
        self._name = name

    def formulate(self) -> list[str]:
        return [self._name]

    def __call__(self, *_args: object, **_kwargs: object) -> None:
        return None


CrossDecisionFactory = cabc.Callable[..., object]
DummyCommandFactory = cabc.Callable[..., _DummyCommand]


def _cross_decision(
    main_module: ModuleType, *, use_cross: bool, requires_container: bool = False
) -> object:
    return main_module._CrossDecision(  # type: ignore[attr-defined]
        cross_path="/usr/bin/cross" if use_cross else None,
        cross_version="0.2.5",
        use_cross=use_cross,
        cross_toolchain_spec="+stable",
        cargo_toolchain_spec="+stable",
        use_cross_local_backend=False,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker" if use_cross else None,
        requires_cross_container=requires_container,
    )


@pytest.fixture
def dummy_command_factory() -> DummyCommandFactory:
    """Return a factory that builds dummy command objects for tests."""

    def factory(name: str = "dummy") -> _DummyCommand:
        return _DummyCommand(name)

    return factory


@pytest.fixture
def cross_decision_factory() -> CrossDecisionFactory:
    """Return a factory that builds _CrossDecision values for tests."""

    def factory(
        main_module: ModuleType, *, use_cross: bool, requires_container: bool = False
    ) -> object:
        return _cross_decision(
            main_module, use_cross=use_cross, requires_container=requires_container
        )

    return factory


@pytest.fixture
def echo_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> cabc.Callable[[ModuleType], list[tuple[str, bool]]]:
    """Return a helper that patches ``typer.echo`` and records messages."""

    def install(module: ModuleType) -> list[tuple[str, bool]]:
        messages: list[tuple[str, bool]] = []

        def fake_echo(message: str, *, err: bool = False) -> None:
            messages.append((message, err))

        monkeypatch.setattr(module.typer, "echo", fake_echo)
        return messages

    return install


@pytest.fixture
def module_harness(monkeypatch: pytest.MonkeyPatch) -> HarnessFactory:
    """Return a factory that wraps a module with a harness and recorder."""

    def factory(module: ModuleType) -> ModuleHarness:
        harness = ModuleHarness(module, monkeypatch)
        if hasattr(module, "run_cmd"):
            harness.patch_run_cmd()
        return harness

    return factory


@pytest.fixture
def setup_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a temporary Cargo manifest and switch into its directory."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RBR_MANIFEST_PATH", raising=False)
    return manifest


@pytest.fixture
def patch_common_main_deps(
    main_module: ModuleType, module_harness: HarnessFactory
) -> ModuleHarness:
    """Provide a harness with the common main() dependencies patched."""
    harness = module_harness(main_module)
    harness.patch_attr("_ensure_rustup_exec", lambda: "/usr/bin/rustup")
    harness.patch_attr("_resolve_toolchain", lambda *_: ("stable", ["stable"]))
    harness.patch_attr("_ensure_target_installed", lambda *_: True)
    harness.patch_attr("configure_windows_linkers", lambda *_, **__: None)
    harness.patch_attr("_configure_cross_container_engine", lambda *_: (None, None))
    harness.patch_attr("_restore_container_engine", lambda *_, **__: None)
    return harness


@pytest.fixture
def ensure_toolchain_ready() -> cabc.Callable[[str, str], None]:
    """Return a helper that ensures the requested toolchain is installed."""

    def _ensure(toolchain_version: str, host_target: str) -> None:
        rustup_path = shutil.which("rustup")
        if rustup_path is None:  # pragma: no cover - guarded by caller checks
            pytest.skip("rustup not installed")
        if not Path(rustup_path).is_file():
            pytest.skip(f"rustup missing at resolved path: {rustup_path}")
        try:
            _, stdout, _ = typ.cast(
                "tuple[int, str, str]",
                run_cmd(
                    local[rustup_path]["toolchain", "list"],
                    method="run",
                ),
            )
        except (OSError, ProcessExecutionError) as exc:  # pragma: no cover - env guard
            pytest.skip(f"rustup unavailable on runner: {exc}")
        installed_names = [
            line.split()[0] for line in stdout.splitlines() if line.strip()
        ]
        expected = {
            toolchain_version,
            f"{toolchain_version}-{host_target}",
        }
        if all(name not in expected for name in installed_names):
            install_spec = (
                f"{toolchain_version}-{host_target}"
                if sys.platform == "win32" and host_target.endswith("-pc-windows-gnu")
                else toolchain_version
            )
            run_cmd(
                local[rustup_path][
                    "toolchain",
                    "install",
                    install_spec,
                    "--profile",
                    "minimal",
                ]
            )

    return _ensure


@pytest.fixture
def cross_module() -> ModuleType:
    """Load the cross manager module with dependency guards."""
    return _load_module(
        "cross_manager.py",
        "rbr_cross",
        deps=(
            ("typer", "Typer"),
            ("packaging", "version"),
        ),
    )


@pytest.fixture
def main_module() -> ModuleType:
    """Load the action entrypoint module with dependency guards."""
    return _load_module(
        "main.py",
        "rbr_main",
        deps=(
            ("typer", "Typer"),
            ("packaging", "version"),
        ),
    )


@pytest.fixture
def runtime_module() -> ModuleType:
    """Load the runtime detection helpers with dependency guards."""
    return _load_module(
        "runtime.py",
        "rbr_runtime",
        deps=(("typer", "Typer"),),
    )


@pytest.fixture
def action_setup_module() -> ModuleType:
    """Load the composite action setup helpers."""
    return _load_module(
        "action_setup.py",
        "rbr_action_setup",
        deps=(("typer", "Typer"),),
    )


@pytest.fixture
def toolchain_module() -> ModuleType:
    """Load the toolchain helper module."""
    return _load_module("toolchain.py", "rbr_toolchain")


@pytest.fixture
def utils_module() -> ModuleType:
    """Load the utility helper module."""
    return _load_module("utils.py", "rbr_utils")


@pytest.fixture
def uncapture_if_verbose(
    request: pytest.FixtureRequest, capfd: pytest.CaptureFixture[str]
) -> IteratorNone:
    """Disable output capture when pytest runs with increased verbosity."""
    if request.config.get_verbosity() > 0:
        with capfd.disabled():
            yield
    else:
        yield


@pytest.fixture(scope="module")
def packaging_config() -> _PackagingConfig:
    """Return the static metadata for the sample packaging project."""
    return _DEFAULT_CONFIG


@pytest.fixture(scope="module")
def packaging_target() -> str:
    """Return the Rust target triple used in integration tests."""
    return _DEFAULT_TARGET


@pytest.fixture(scope="module")
def packaging_project_paths(
    tmp_path_factory: pytest.TempPathFactory,
) -> _PackagingProject:
    """Resolve the filesystem layout for packaging integration tests."""
    base_project = _packaging_project()
    clone_root = Path(tmp_path_factory.mktemp("packaging-project"))
    return _clone_packaging_project(clone_root, base_project)


@pytest.fixture(scope="module")
def build_artefacts(
    packaging_project_paths: _PackagingProject,
    packaging_target: str,
    packaging_config: _PackagingConfig,
) -> _BuildArtefacts:
    """Ensure the sample project is built for the requested target."""
    return _build_release_artefacts(
        packaging_project_paths,
        packaging_target,
        config=packaging_config,
    )


@pytest.fixture(scope="module")
def packaged_artefacts(
    packaging_project_paths: _PackagingProject,
    build_artefacts: _BuildArtefacts,
    packaging_config: _PackagingConfig,
) -> typ.Mapping[str, Path]:
    """Package the built project as both .deb and .rpm artefacts."""
    return _package_project(
        packaging_project_paths,
        build_artefacts,
        config=packaging_config,
        formats=("deb", "rpm"),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Drop legacy xfail marks for Windows smoke tests now passing."""
    for item in items:
        nodeid = getattr(item, "nodeid", "")
        if WINDOWS_SMOKE_TEST not in nodeid or "-pc-windows-" not in nodeid:
            continue
        original_count = len(item.own_markers)
        filtered_markers = [
            mark
            for mark in item.own_markers
            if not (
                mark.name == "xfail"
                and mark.kwargs.get("reason") == WINDOWS_XFAIL_REASON
            )
        ]
        if len(filtered_markers) != original_count:
            item.own_markers[:] = filtered_markers
