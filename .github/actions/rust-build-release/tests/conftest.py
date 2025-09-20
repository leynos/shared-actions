"""Shared fixtures and helpers for rust-build-release tests."""

from __future__ import annotations

import collections.abc as cabc
import importlib
import importlib.util
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
from _packaging_utils import (
    DEFAULT_CONFIG,
    DEFAULT_TARGET,
    BuildArtifacts,
    PackagingConfig,
    PackagingProject,
    build_release_artifacts,
    package_project,
    packaging_project,
)

from cmd_utils import run_cmd

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

if typ.TYPE_CHECKING:
    import types as types_module

    ModuleType = types_module.ModuleType
else:  # pragma: no cover - type checking only
    ModuleType = typ.Any


IteratorNone = typ.Iterator[None]

WINDOWS_SMOKE_TEST = "test_action_builds_release_binary_and_manpage"
WINDOWS_XFAIL_REASON = (
    "Known failure on Windows; see https://github.com/leynos/shared-actions/issues/93"
)


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
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
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
        self, side_effect: cabc.Callable[[list[str]], object | None] | None = None
    ) -> None:
        """Patch ``run_cmd`` to record calls and execute an optional side effect."""

        def fake(cmd: list[str]) -> object | None:
            self.calls.append(cmd)
            return side_effect(cmd) if side_effect is not None else None

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


HarnessFactory = cabc.Callable[[ModuleType], ModuleHarness]


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
def ensure_toolchain_ready() -> cabc.Callable[[str, str], None]:
    """Return a helper that ensures the requested toolchain is installed."""

    def _ensure(toolchain_version: str, host_target: str) -> None:
        rustup_path = shutil.which("rustup")
        if rustup_path is None:  # pragma: no cover - guarded by caller checks
            pytest.skip("rustup not installed")
        result = subprocess.run(  # noqa: S603
            [rustup_path, "toolchain", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        installed_names = [
            line.split()[0] for line in result.stdout.splitlines() if line.strip()
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
                [
                    "rustup",
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
def packaging_config() -> PackagingConfig:
    """Return the static metadata for the sample packaging project."""
    return DEFAULT_CONFIG


@pytest.fixture(scope="module")
def packaging_target() -> str:
    """Return the Rust target triple used in integration tests."""
    return DEFAULT_TARGET


@pytest.fixture(scope="module")
def packaging_project_paths() -> PackagingProject:
    """Resolve the filesystem layout for packaging integration tests."""
    return packaging_project()


@pytest.fixture(scope="module")
def build_artifacts(
    packaging_project_paths: PackagingProject,
    packaging_target: str,
    packaging_config: PackagingConfig,
) -> BuildArtifacts:
    """Ensure the sample project is built for the requested target."""
    return build_release_artifacts(
        packaging_project_paths,
        packaging_target,
        config=packaging_config,
    )


@pytest.fixture(scope="module")
def packaged_artifacts(
    packaging_project_paths: PackagingProject,
    build_artifacts: BuildArtifacts,
    packaging_config: PackagingConfig,
) -> typ.Mapping[str, Path]:
    """Package the built project as both .deb and .rpm artefacts."""
    return package_project(
        packaging_project_paths,
        build_artifacts,
        config=packaging_config,
        formats=("deb", "rpm"),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Drop legacy xfail marks for Windows smoke tests now passing."""

    for item in items:
        nodeid = getattr(item, "nodeid", "")
        if (
            WINDOWS_SMOKE_TEST not in nodeid
            or "-pc-windows-" not in nodeid
        ):
            continue
        xfail_marks = [
            mark
            for mark in item.iter_markers(name="xfail")
            if mark in item.own_markers
        ]
        if not xfail_marks:
            continue
        drop_marks = [
            mark
            for mark in xfail_marks
            if WINDOWS_XFAIL_REASON in str(mark.kwargs.get("reason", ""))
        ]
        if not drop_marks:
            continue
        keep_marks = [mark for mark in xfail_marks if mark not in drop_marks]
        item.remove_marker("xfail")
        for mark in keep_marks:
            item.add_marker(pytest.mark.xfail(*mark.args, **mark.kwargs))
