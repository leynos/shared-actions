"""Pytest configuration for shared actions tests."""

from __future__ import annotations

import collections
import collections.abc as cabc
import importlib.util
import os
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest


def _default_action_path() -> str:
    """Return the repository action directory used for cmd_utils discovery."""
    return str(Path(__file__).resolve().parent / ".github" / "actions")


os.environ.setdefault("GITHUB_ACTION_PATH", _default_action_path())

_VLP_TESTS_ROOT = (
    Path(__file__).resolve().parent / ".github" / "actions" / "validate-linux-packages"
)
if str(_VLP_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_VLP_TESTS_ROOT))

_ACTIONS_ROOT = Path(__file__).resolve().parent / ".github" / "actions"
_VLP_TESTS_PACKAGE = _VLP_TESTS_ROOT / "tests"
_VLP_TESTS_INIT = _VLP_TESTS_PACKAGE / "__init__.py"
if _VLP_TESTS_INIT.exists():
    package_paths = [str(_VLP_TESTS_PACKAGE)]
    for action_dir in _ACTIONS_ROOT.iterdir():
        candidate = action_dir / "tests"
        if candidate.is_dir():
            package_paths.append(str(candidate))

    module = sys.modules.get("tests")
    if module is None:
        spec = importlib.util.spec_from_file_location("tests", _VLP_TESTS_INIT)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            module.__path__ = package_paths
            sys.modules["tests"] = module
            spec.loader.exec_module(module)
    else:
        existing = list(dict.fromkeys(getattr(module, "__path__", [])))
        for path in package_paths:
            if path not in existing:
                existing.append(path)
        module.__path__ = existing

CMD_MOX_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="cmd-mox does not support Windows"
)
HAS_UV = shutil.which("uv") is not None

REQUIRES_UV = pytest.mark.usefixtures("require_uv")

sys.modules.setdefault("shared_actions_conftest", sys.modules[__name__])


class CmdDouble(typ.Protocol):
    """Contract for cmd-mox doubles that record expectations and behaviour."""

    call_count: int

    def with_args(self, *args: str) -> typ.Self:
        """Set the expected argv for the double."""
        ...

    def returns(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        **_: object,
    ) -> typ.Self:
        """Provide canned output for the command invocation."""
        ...

    def runs(self, handler: cabc.Callable[[object], tuple[str, str, int]]) -> typ.Self:
        """Execute a handler when the double is invoked."""
        ...


class CmdMoxEnvironment(typ.Protocol):
    """Subset of :class:`cmd_mox.EnvironmentManager` used in tests."""

    shim_dir: Path | None


class CmdMox(typ.Protocol):
    """Typed façade for the cmd-mox pytest fixture used in tests."""

    environment: CmdMoxEnvironment

    def stub(self, command: str) -> CmdDouble:
        """Register a stubbed command double."""
        ...

    def spy(self, command: str) -> CmdDouble:
        """Register a spying command double."""
        ...

    def replay(self) -> None:
        """Activate the recorded doubles."""
        ...

    def verify(self) -> None:
        """Assert that recorded expectations were satisfied."""
        ...


def _shim_path(cmd_mox: CmdMox, command: str) -> str:
    """Return the shim path for ``command`` ensuring the environment is ready."""
    shim_dir = cmd_mox.environment.shim_dir
    if shim_dir is None:  # pragma: no cover - defensive guard
        msg = "cmd-mox shim directory is unavailable"
        raise RuntimeError(msg)
    return str(shim_dir / command)


@pytest.fixture
def require_uv() -> None:
    """Skip tests that exercise uv when the CLI is unavailable."""
    if not HAS_UV:
        pytest.skip("uv CLI not installed")


def _register_cross_version_stub(
    cmd_mox: CmdMox,
    stdout: str | cabc.Iterable[str] = "cross 0.2.5\n",
) -> str:
    """Register a stub for ``cross --version`` and return the shim path."""
    if isinstance(stdout, str):
        cmd_mox.stub("cross").with_args("--version").returns(stdout=stdout)
    else:
        outputs = collections.deque(stdout)
        last = outputs[-1] if outputs else "cross 0.2.5\n"

        def _handler(_invocation: object) -> tuple[str, str, int]:
            data = outputs.popleft() if outputs else last
            return data, "", 0

        cmd_mox.stub("cross").with_args("--version").runs(_handler)
    return _shim_path(cmd_mox, "cross")


def _register_rustup_toolchain_stub(
    cmd_mox: CmdMox,
    stdout: str,
) -> str:  # pragma: no cover - helper
    """Register a stub for ``rustup toolchain list`` and return the shim path."""
    cmd_mox.stub("rustup").with_args("toolchain", "list").returns(stdout=stdout)
    return _shim_path(cmd_mox, "rustup")


def _register_docker_info_stub(
    cmd_mox: CmdMox,
    *,
    exit_code: int = 0,
) -> str:  # pragma: no cover - helper
    """Register a stub for ``docker info`` and return the shim path."""
    cmd_mox.stub("docker").with_args("info").returns(exit_code=exit_code)
    return _shim_path(cmd_mox, "docker")


def _register_podman_info_stub(
    cmd_mox: CmdMox,
    *,
    exit_code: int = 0,
) -> str:  # pragma: no cover - helper
    """Register a stub for ``podman info`` and return the shim path."""
    cmd_mox.stub("podman").with_args("info").returns(exit_code=exit_code)
    return _shim_path(cmd_mox, "podman")


if sys.platform != "win32":  # pragma: win32 no cover - windows lacks cmd-mox
    pytest_plugins = ("cmd_mox.pytest_plugin",)
else:

    @pytest.fixture
    def cmd_mox() -> typ.NoReturn:  # pragma: win32 no cover
        """Skip tests that rely on cmd-mox on Windows."""
        pytest.skip("cmd-mox does not support Windows")
        unreachable = "unreachable"
        raise RuntimeError(unreachable)
