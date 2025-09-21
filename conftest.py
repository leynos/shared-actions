"""Pytest configuration for shared actions tests."""

from __future__ import annotations

import collections
import collections.abc as cabc
import shutil
import sys
from pathlib import Path
from typing import NoReturn, Protocol

import pytest

CMD_MOX_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="cmd-mox does not support Windows"
)
HAS_UV = shutil.which("uv") is not None

REQUIRES_UV = pytest.mark.usefixtures("require_uv")

sys.modules.setdefault("shared_actions_conftest", sys.modules[__name__])


class CmdDouble(Protocol):
    """Contract for cmd-mox doubles that record expectations and behaviour."""

    call_count: int

    def with_args(self, *args: str) -> CmdDouble:
        """Set the expected argv for the double."""

    def returns(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        **_: object,
    ) -> CmdDouble:
        """Provide canned output for the command invocation."""

    def runs(
        self, handler: cabc.Callable[[object], tuple[str, str, int]]
    ) -> CmdDouble:
        """Execute a handler when the double is invoked."""


class CmdMox(Protocol):
    """Typed façade for the cmd-mox pytest fixture used in tests."""

    shim_dir: Path

    def stub(self, command: str) -> CmdDouble:
        """Register a stubbed command double."""

    def spy(self, command: str) -> CmdDouble:
        """Register a spying command double."""

    def replay(self) -> None:
        """Activate the recorded doubles."""

    def verify(self) -> None:
        """Assert that recorded expectations were satisfied."""


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
    return str(cmd_mox.shim_dir / "cross")


def _register_rustup_toolchain_stub(
    cmd_mox: CmdMox,
    stdout: str,
) -> str:  # pragma: no cover - helper
    """Register a stub for ``rustup toolchain list`` and return the shim path."""
    cmd_mox.stub("rustup").with_args("toolchain", "list").returns(stdout=stdout)
    return str(cmd_mox.shim_dir / "rustup")


def _register_docker_info_stub(
    cmd_mox: CmdMox,
    *,
    exit_code: int = 0,
) -> str:  # pragma: no cover - helper
    """Register a stub for ``docker info`` and return the shim path."""
    cmd_mox.stub("docker").with_args("info").returns(exit_code=exit_code)
    return str(cmd_mox.shim_dir / "docker")


if sys.platform != "win32":  # pragma: win32 no cover - Windows lacks cmd-mox support
    pytest_plugins = ("cmd_mox.pytest_plugin",)
else:

    @pytest.fixture
    def cmd_mox() -> NoReturn:  # pragma: win32 no cover - fixture only used on Windows
        """Skip tests that rely on cmd-mox on Windows."""
        pytest.skip("cmd-mox does not support Windows")
