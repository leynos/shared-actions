"""Smoke tests for rust-build-release action."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

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
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

runtime_module = importlib.import_module("runtime")
detect_host_target = runtime_module.detect_host_target
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


HOST_TARGET = detect_host_target()

_targets: list[str] = [HOST_TARGET]

if HOST_TARGET.endswith("-unknown-linux-gnu") and (
    runtime_available("docker", cwd=BASE_PROJECT.project_dir)
    or runtime_available("podman", cwd=BASE_PROJECT.project_dir)
):
    _targets.append("aarch64-unknown-linux-gnu")

if sys.platform == "win32":
    zig_path = shutil.which("zig")
    if shutil.which("x86_64-w64-mingw32-gcc") or zig_path:
        _targets.append("x86_64-pc-windows-gnu")
    if shutil.which("aarch64-w64-mingw32-gcc") or zig_path:
        _targets.append("aarch64-pc-windows-gnu")


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


def run_script(script: Path, *args: str, cwd: Path | None = None) -> RunResult:
    """Execute *script* in *cwd* and return the completed process."""
    python_exe = sys.executable or shutil.which("python") or "python"
    uv_path = shutil.which("uv")
    if uv_path is not None:
        command = local[uv_path]["run", python_exe, str(script)]
    else:
        command = local[python_exe][str(script)]
    if args:
        command = command[list(args)]
    return run_plumbum_command(command, method="run", cwd=cwd)


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
    res = run_script(script, target, cwd=project_dir)
    assert res.returncode == 0
    binary = project_dir / (
        f"target/{target}/release/rust-toy-app"
        + (".exe" if "windows" in target else "")
    )
    assert binary.exists()
    manpage_glob = project_dir.glob(
        f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
    )
    assert any(manpage_glob)


def test_fails_without_target(packaging_project_paths: _PackagingProject) -> None:
    """Script exits with an error when no target is provided."""
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    project_dir = packaging_project_paths.project_dir
    if shutil.which("rustup") is None:
        pytest.skip("rustup not installed")
    res = run_script(script, cwd=project_dir)
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
