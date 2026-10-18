"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from syspath_hack import prepend_to_syspath

from cmd_utils_importer import import_cmd_utils
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from types import ModuleType

    from ._packaging_utils import PackagingProject as _PackagingProject
else:  # pragma: no cover - type checking helper
    ModuleType = typ.Any
    _PackagingProject = typ.Any


def _import_packaging_utils() -> ModuleType:
    try:
        from . import _packaging_utils as pkg_utils
    except ImportError:  # pragma: no cover - fallback for direct invocation
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
            msg = "failed to import packaging utils"
            raise ImportError(msg) from None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        pkg_utils = module
    return pkg_utils


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
prepend_to_syspath(SRC_DIR)

runtime_module = importlib.import_module("runtime")
runtime_available = runtime_module.runtime_available

_packaging_utils = _import_packaging_utils()
BASE_PROJECT = _packaging_utils.packaging_project()

TOOLCHAIN_VERSION = (
    (Path(__file__).resolve().parents[1] / "TOOLCHAIN_VERSION")
    .read_text(encoding="utf-8")
    .strip()
)

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")
LINUX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="requires Linux")
WINDOWS_KNOWN_FAILURE = pytest.mark.xfail(
    sys.platform == "win32",
    reason=(
        "Known failure on Windows; see "
        "https://github.com/leynos/shared-actions/issues/93"
    ),
    strict=True,
)


HOST_TARGET = runtime_module.DEFAULT_HOST_TARGET


def _has_container_runtime() -> bool:
    """Probe Docker before trying Podman as a fallback.

    ```python
    # When Docker is available, Podman is not probed:
    _has_container_runtime()  # True
    ```
    """
    return runtime_available(
        "docker", cwd=BASE_PROJECT.project_dir
    ) or runtime_available("podman", cwd=BASE_PROJECT.project_dir)


def _supports_linux_cross() -> bool:
    """Require a GNU/Linux host before probing container support.

    ```python
    # For a non-GNU/Linux HOST_TARGET, no runtime probe runs:
    _supports_linux_cross()  # False
    ```
    """
    return HOST_TARGET.endswith("-unknown-linux-gnu") and _has_container_runtime()


def _windows_cross_targets() -> list[str]:
    """List supported Windows targets after probing shared Zig support once.

    ```python
    # With Zig installed, both targets are selected in this order:
    _windows_cross_targets()  # ["x86_64-pc-windows-gnu", "aarch64-pc-windows-gnu"]
    ```
    """
    targets: list[str] = []
    zig_path = shutil.which("zig")
    if shutil.which("x86_64-w64-mingw32-gcc") or zig_path:
        targets.append("x86_64-pc-windows-gnu")
    if shutil.which("aarch64-w64-mingw32-gcc") or zig_path:
        targets.append("aarch64-pc-windows-gnu")
    return targets


def _available_targets() -> list[str]:
    """Query installed build support while retaining host-first target order.

    ```python
    # On a GNU/Linux host with neither Docker nor Podman available:
    _available_targets()  # [HOST_TARGET], with no cross target appended
    ```
    """
    targets = [HOST_TARGET]
    if _supports_linux_cross():
        targets.append("aarch64-unknown-linux-gnu")
    if sys.platform == "win32":
        targets.extend(_windows_cross_targets())
    return targets


_targets = _available_targets()


def _param_for_target(target: str) -> object:
    """Return a parametrization entry for *target* with platform marks."""
    marks: list[pytest.MarkDecorator] = []
    if target != HOST_TARGET and target.endswith("-unknown-linux-gnu"):
        marks.append(LINUX_ONLY)
    if target.endswith(("-pc-windows-gnu", "-pc-windows-msvc")):
        marks.extend((WINDOWS_ONLY, WINDOWS_KNOWN_FAILURE))
    if marks:
        return pytest.param(target, marks=tuple(marks))
    return pytest.param(target)


TARGET_PARAMS = [_param_for_target(target) for target in _targets]


if typ.TYPE_CHECKING:
    from cmd_utils import RunResult
else:
    RunResult = import_cmd_utils().RunResult


def run_script(
    script: Path, *args: str, target_root: Path | None, cwd: Path | None = None
) -> RunResult:
    """Execute *script* with the fixture root only in its child environment.

    ```python
    result = run_script(script, target, target_root=Path("/build/shared"), cwd=project)
    # The child receives CARGO_TARGET_DIR=/build/shared; the parent is unchanged.
    ```
    """
    if target_root is None:
        message = "integration project target root was not frozen"
        raise ValueError(message)
    python_exe = sys.executable or shutil.which("python") or "python"
    uv_path = shutil.which("uv")
    if uv_path is not None:
        command = local[uv_path]["run", python_exe, str(script)]
    else:
        command = local[python_exe][str(script)]
    if args:
        command = command[list(args)]
    return run_plumbum_command(
        command,
        method="run",
        cwd=cwd,
        env=_packaging_utils._build_child_environment(
            target_root,
            local.env,
            os.environ,
        ),
    )


@pytest.mark.parametrize("target", TARGET_PARAMS)
def test_action_builds_release_binary_and_manpage(
    target: str,
    ensure_toolchain_ready: typ.Callable[[str, str], None],
    packaging_project_paths: _PackagingProject,
) -> None:
    """The build script produces a release binary and man page."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    # On Windows, ensure a linker exists for GNU aarch64 targets.
    if (
        sys.platform == "win32"
        and target.endswith("-pc-windows-gnu")
        and "aarch64" in target
        and shutil.which("aarch64-w64-mingw32-gcc") is None
        and shutil.which("zig") is None
    ):
        pytest.skip(
            "aarch64 GNU linker not available (need aarch64-w64-mingw32-gcc or zig)"
        )
    if (
        sys.platform != "win32"
        and target != HOST_TARGET
        and not any(
            runtime_available(runtime, cwd=project_dir)
            for runtime in ("docker", "podman")
        )
    ):
        pytest.skip("container runtime required for cross build")
    ensure_toolchain_ready(TOOLCHAIN_VERSION, HOST_TARGET)
    res = run_script(
        script, target, target_root=packaging_project_paths.target_root, cwd=project_dir
    )
    assert res.returncode == 0, res.stderr
    target_root = packaging_project_paths.target_root
    assert target_root is not None
    binary = target_root / (
        f"{target}/release/rust-toy-app" + (".exe" if "windows" in target else "")
    )
    assert binary.exists()
    manpage = target_root / f"generated-man/{target}/release/rust-toy-app.1"
    assert manpage.exists()


def test_fails_without_target(packaging_project_paths: _PackagingProject) -> None:
    """Script exits with an error when no target is provided."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    res = run_script(
        script, target_root=packaging_project_paths.target_root, cwd=project_dir
    )
    assert res.returncode != 0
    assert "RBR_TARGET=<unset>" in res.stderr


def test_fails_for_invalid_toolchain(
    packaging_project_paths: _PackagingProject,
) -> None:
    """Script surfaces rustup errors for invalid toolchains."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    res = run_script(
        script,
        HOST_TARGET,
        "--toolchain",
        "bogus",
        target_root=packaging_project_paths.target_root,
        cwd=project_dir,
    )
    assert res.returncode != 0
    assert "requested toolchain 'bogus' not installed" in res.stderr


def test_fails_for_unsupported_target(
    ensure_toolchain_ready: typ.Callable[[str, str], None],
    packaging_project_paths: _PackagingProject,
) -> None:
    """Script errors when a valid toolchain lacks the requested target."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    ensure_toolchain_ready(TOOLCHAIN_VERSION, HOST_TARGET)
    bogus_target = "bogus-target-1234"
    res = run_script(
        script,
        bogus_target,
        "--toolchain",
        TOOLCHAIN_VERSION,
        target_root=packaging_project_paths.target_root,
        cwd=project_dir,
    )
    assert res.returncode != 0
    assert f"target '{bogus_target}'" in res.stderr


def test_accepts_full_toolchain_spec(
    ensure_toolchain_ready: typ.Callable[[str, str], None],
    packaging_project_paths: _PackagingProject,
) -> None:
    """Script accepts fully qualified toolchain triples."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    ensure_toolchain_ready(TOOLCHAIN_VERSION, HOST_TARGET)
    full_toolchain = f"{TOOLCHAIN_VERSION}-{HOST_TARGET}"
    res = run_script(
        script,
        HOST_TARGET,
        "--toolchain",
        full_toolchain,
        target_root=packaging_project_paths.target_root,
        cwd=project_dir,
    )
    if res.returncode != 0:
        stdout = res.stdout or ""
        stderr = res.stderr or ""
        print("--- stdout ---", file=sys.stderr)
        print(stdout, file=sys.stderr)
        print("--- stderr ---", file=sys.stderr)
        print(stderr, file=sys.stderr)
    assert res.returncode == 0, (
        f"process exited with {res.returncode}\n"
        f"stdout:\n{res.stdout or ''}\n"
        f"stderr:\n{res.stderr or ''}"
    )


@pytest.mark.parametrize(
    "relative", [False, True], ids=["absolute", "cargo-cwd-relative"]
)
def test_target_root_preserves_inherited_cargo_path(*, relative: bool) -> None:
    """Artifact checks resolve the same environment path as Cargo's cwd."""
    project = Path(__file__).resolve().parents[4] / "target" / "fixture-project"
    inherited = "shared-build" if relative else str(project / "absolute-build")
    expected = project / "shared-build" if relative else Path(inherited)
    assert _packaging_utils._effective_target_root(project, inherited) == expected


def test_missing_target_root_allocates_unique_repository_directory() -> None:
    """Absent Cargo routing gets independent ignored roots outside temporary storage."""
    project = Path(__file__).resolve().parents[4] / "rust-toy-app"
    first = _packaging_utils._effective_target_root(project, None)
    second = _packaging_utils._effective_target_root(project, None)
    assert first != second
    assert first.is_relative_to(project.parent / "target" / "pytest-integration")
    assert second.is_relative_to(project.parent / "target" / "pytest-integration")
    assert not first.resolve().is_relative_to(
        Path("/tmp").resolve(),  # noqa: S108 - independent rejection-boundary assertion.
    )
    assert not second.resolve().is_relative_to(
        Path("/tmp").resolve(),  # noqa: S108 - independent rejection-boundary assertion.
    )


@pytest.mark.parametrize("inherited", ["", None], ids=["empty", "absent"])
def test_target_root_default_is_unique(inherited: str | None) -> None:
    """Both Cargo-default forms receive isolated repository-owned fixture roots."""
    project = Path(__file__).resolve().parents[4] / "rust-toy-app"
    root = _packaging_utils._effective_target_root(project, inherited)
    assert root.is_relative_to(project.parent / "target" / "pytest-integration")
    assert root.is_dir()


def test_temporary_target_root_is_rejected() -> None:
    """An inherited temporary build route fails before launching a compiler."""
    project = Path(__file__).resolve().parents[4] / "rust-toy-app"
    with pytest.raises(
        ValueError, match="integration Cargo target must be outside /tmp"
    ):
        _packaging_utils._effective_target_root(
            project,
            "/tmp/forbidden-cargo-target",  # noqa: S108 - negative input; never allocated.
        )


@pytest.mark.parametrize("target", ["D:build", "C:", "z:relative/path"])
def test_packaging_drive_relative_target_is_rejected(target: str) -> None:
    """The packaging resolver fails before compiling with ambiguous drive paths."""
    project = Path(__file__).resolve().parents[4] / "rust-toy-app"
    with pytest.raises(ValueError, match="drive-relative Cargo target is ambiguous"):
        _packaging_utils._effective_target_root(project, target)


@pytest.mark.parametrize("target", ["build", "../build", "D:/build", "/build"])
def test_packaging_ordinary_target_shapes_are_preserved(target: str) -> None:
    """Pure Windows shape validation accepts ordinary relative and absolute roots."""
    _packaging_utils._reject_drive_relative_target(target)


@pytest.mark.parametrize("manager", [None, "/managed/build-limits-rustc"])
@pytest.mark.parametrize("engine", [None, "podman"])
def test_package_build_child_environment_preserves_admission(
    manager: str | None, engine: str | None
) -> None:
    """Preserve parent controls while routing the child target and Podman."""
    inherited = {
        "RUSTDOC": "/managed/build-limits-rustdoc",
        "CARGO_HOME": "/shared/cargo",
        "RUSTC_WORKSPACE_WRAPPER": "/managed/workspace-wrapper",
        "CARGO_TARGET_DIR": "old-target",
        "CROSS_CONTAINER_ENGINE": "docker",
    }
    if manager is not None:
        inherited["RUSTC_WRAPPER"] = manager
    plumbum_environment = {"PLUMBUM_ONLY": "retained", "RUSTDOC": "stale-plumbum-value"}
    before_plumbum = plumbum_environment.copy()
    before = inherited.copy()
    target_root = Path(__file__).resolve().parents[4] / "target" / "contract-only"
    child = _packaging_utils._build_child_environment(
        target_root,
        plumbum_environment,
        inherited,
        container_engine=engine,
    )
    assert child == {
        **before_plumbum,
        **before,
        "CARGO_TARGET_DIR": str(target_root),
        "CROSS_CONTAINER_ENGINE": "podman" if engine is not None else "docker",
    }
    assert inherited == before
    assert plumbum_environment == before_plumbum
