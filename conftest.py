"""Pytest configuration for shared actions tests."""

from __future__ import annotations

import collections
import collections.abc as cabc
import sys

import pytest


CMD_MOX_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="cmd-mox does not support Windows"
)

sys.modules.setdefault("shared_actions_conftest", sys.modules[__name__])


def _register_cross_version_stub(
    cmd_mox, stdout: str | cabc.Iterable[str] = "cross 0.2.5\n"
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
    return str(cmd_mox.environment.shim_dir / "cross")


def _register_rustup_toolchain_stub(
    cmd_mox, stdout: str
) -> str:  # pragma: no cover - helper
    """Register a stub for ``rustup toolchain list`` and return the shim path."""

    cmd_mox.stub("rustup").with_args("toolchain", "list").returns(stdout=stdout)
    return str(cmd_mox.environment.shim_dir / "rustup")


def _register_docker_info_stub(
    cmd_mox, *, exit_code: int = 0
) -> str:  # pragma: no cover - helper
    """Register a stub for ``docker info`` and return the shim path."""

    cmd_mox.stub("docker").with_args("info").returns(exit_code=exit_code)
    return str(cmd_mox.environment.shim_dir / "docker")


if sys.platform != "win32":  # pragma: win32 no cover - Windows lacks cmd-mox support
    pytest_plugins = ("cmd_mox.pytest_plugin",)
else:

    @pytest.fixture()
    def cmd_mox():  # pragma: win32 no cover - fixture only used on Windows
        """Skip tests that rely on cmd-mox on Windows."""

        pytest.skip("cmd-mox does not support Windows")
