"""Pytest configuration for shared actions tests."""

from __future__ import annotations

import collections
import collections.abc as cabc
import os
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest


def _default_action_path() -> str:
    """Return the repository action directory used for cmd_utils discovery."""
    return str(Path(__file__).resolve().parent / ".github" / "actions")


def _pin_action_path() -> None:
    """Point ``GITHUB_ACTION_PATH`` at this repository's actions directory.

    The suite discovers action scripts relative to ``GITHUB_ACTION_PATH``.
    When the tests run inside a composite action — as they do when CI
    generates coverage through the shared ``generate-coverage`` action — the
    runner sets ``GITHUB_ACTION_PATH`` to *that* action's directory. Discovery
    then misfires for tests belonging to other actions (e.g.
    ``release-to-pypi-uv`` looks for its scripts under
    ``generate-coverage/scripts``). Re-point the variable at the repository
    actions root in that case; otherwise preserve the historical default,
    which sets the variable only when it is unset.
    """
    repo_actions = _default_action_path()
    ambient = os.environ.get("GITHUB_ACTION_PATH")
    if ambient and Path(ambient).resolve().is_relative_to(Path(repo_actions).resolve()):
        os.environ["GITHUB_ACTION_PATH"] = repo_actions
    else:
        os.environ.setdefault("GITHUB_ACTION_PATH", repo_actions)


_pin_action_path()

CMD_MOX_UNSUPPORTED = pytest.mark.skipif(
    sys.platform == "win32", reason="cmd-mox does not support Windows"
)
HAS_UV = shutil.which("uv") is not None

REQUIRES_UV = pytest.mark.usefixtures("require_uv")

sys.modules.setdefault("shared_actions_conftest", sys.modules[__name__])


def _enable_cmd_mox_replay_idempotence() -> None:
    """Make cmd-mox ``replay()`` a no-op when already in replay phase.

    cmd-mox v0.2.x exposes controller phase state and can raise if ``replay()``
    is called more than once. This repository has existing tests/helpers that
    may invoke ``replay()`` repeatedly, so normalize this edge to preserve
    historical test semantics while keeping other lifecycle checks intact.
    """
    if sys.platform == "win32":  # pragma: no cover - cmd-mox unavailable
        return
    try:
        from cmd_mox.controller import CmdMox as CmdMoxController
        from cmd_mox.controller import Phase
    except ModuleNotFoundError:  # pragma: no cover - defensive import guard
        return

    if getattr(CmdMoxController.replay, "__cmd_mox_replay_guard__", False):
        return

    original_replay = CmdMoxController.replay

    def _replay_with_phase_guard(self: CmdMoxController) -> None:
        phase = getattr(self, "phase", None)
        if phase == Phase.REPLAY:
            return
        original_replay(self)

    _replay_with_phase_guard.__cmd_mox_replay_guard__ = True
    CmdMoxController.replay = _replay_with_phase_guard


class _WritableStream(typ.Protocol):
    """Minimal output-stream contract the reply writer relies on."""

    def write(self, data: bytes) -> int:
        """Write *data* and return the number of bytes accepted."""
        ...

    def flush(self) -> None:
        """Flush buffered output."""
        ...


class _DisconnectTolerantWriter:
    """Reply stream that ignores a client which has already gone away.

    ``_IPCHandler.handle`` writes the reply to ``self.wfile`` with no guard.
    When the client has already closed its read side, that write raises
    :class:`BrokenPipeError`, which escapes the handler and reaches
    ``socketserver.ThreadingMixIn.process_request_thread``. Its default
    ``handle_error`` prints a full server-side traceback — noise that reads
    like a crash even though a client walking away mid-request is ordinary.

    Wrapping the writer keeps the reply path working exactly as before for a
    connected client and turns the disconnect into a silent no-op. The
    swallowed error is not hidden from the connection itself: the write never
    completed, so there is nobody left to receive an error.
    """

    __slots__ = ("_wrapped",)

    def __init__(self, wrapped: _WritableStream) -> None:
        self._wrapped = wrapped

    def write(self, data: bytes) -> int:
        """Write *data*, treating a vanished client as an empty write."""
        try:
            return self._wrapped.write(data)
        except ConnectionError:
            # BrokenPipeError, ConnectionResetError and ConnectionAbortedError
            # all mean the peer is gone. A genuine local fault such as ENOSPC
            # is a plain OSError and deliberately still propagates.
            return 0

    def flush(self) -> None:
        """Forward ``flush`` to the wrapped stream.

        Declared on the wrapper, not left to :meth:`__getattr__`, so the
        method the handler calls is as typed as :meth:`write` and the
        wrapper satisfies :class:`_WritableStream` for a type checker.
        """
        self._wrapped.flush()

    def __getattr__(self, name: str) -> object:
        """Forward every other stream attribute to the wrapped writer."""
        return getattr(self._wrapped, name)


class _RequestHandler(typ.Protocol):
    """The slice of ``socketserver.StreamRequestHandler`` the guard touches."""

    wfile: _WritableStream

    def setup(self) -> None:
        """Prepare the per-connection read and write streams."""
        ...


def _enable_cmd_mox_ipc_disconnect_tolerance() -> None:
    """Stop a disconnected IPC client from printing a server traceback.

    See :class:`_DisconnectTolerantWriter` for the failure this normalizes.
    The guard wraps the handler's output stream once, at connection setup,
    rather than editing upstream source: cmd-mox is a pinned external
    dependency, so the override is applied at this repository's test boundary
    and disappears with the dependency.

    This stands in for a fix cmd-mox does not yet ship. The defect is
    `cmd-mox#256`_ (OPEN), and the upstream fix is ``leynos/cmd-mox#259``
    (OPEN, draft as of 2026-10-10); the newest published release is 0.2.0,
    the version pinned here. At that version the only public server hook is
    ``IPCHandlers``, which exposes the invocation and passthrough callbacks
    and nothing over the reply write, so there is no supported seam to reach
    the guard through. When #259 lands and the pin advances, delete this
    function, the writer, and the regression test that exercises them -- the
    guard becoming redundant is the evidence the pin moved.

    .. _cmd-mox#256: https://github.com/leynos/cmd-mox/issues/256
    """
    if sys.platform == "win32":  # pragma: no cover - cmd-mox unavailable
        return
    try:
        from cmd_mox.ipc.server import _IPCHandler
    except (ModuleNotFoundError, ImportError):  # pragma: no cover - private API
        return

    setup = getattr(_IPCHandler, "setup", None)
    if setup is None or getattr(setup, "__cmd_mox_disconnect_guard__", False):
        return

    def setup_with_disconnect_guard(self: _RequestHandler) -> None:
        original_setup(self)
        self.wfile = _DisconnectTolerantWriter(self.wfile)

    original_setup = setup
    setup_with_disconnect_guard.__cmd_mox_disconnect_guard__ = True
    _IPCHandler.setup = setup_with_disconnect_guard


@pytest.fixture(autouse=True, scope="session")
def _cmd_mox_ipc_disconnect_tolerance() -> None:
    """Apply the cmd-mox disconnect guard once per test session."""
    _enable_cmd_mox_ipc_disconnect_tolerance()


class _PayloadParser(typ.Protocol):
    """The slice of ``cmd_mox.ipc.server._parse_payload`` the guard wraps."""

    def __call__(self, raw: bytes) -> tuple[dict[str, object], str] | None:
        """Decode *raw* into a payload and kind, or ``None`` if unusable."""
        ...


def _enable_cmd_mox_empty_probe_tolerance() -> None:
    """Stop cmd-mox's readiness probe logging a malformed-JSON traceback.

    ``socket_utils._try_socket_connection`` and ``socket_utils.
    cleanup_stale_socket`` both connect and close without writing a single
    byte, because the only question they ask is whether the socket accepts a
    connection. ``_parse_payload`` then decodes zero bytes, ``json.loads(b"")``
    raises ``JSONDecodeError``, and ``logger.exception`` reports that expected
    handshake as malformed input -- with a full traceback, once per server
    start, on every run, healthy or not.

    An empty read is unambiguous. A client with a request to make always sends
    one, so no bytes at all can only be the readiness probe. The override
    returns ``None`` for it, which is already the answer the server gives every
    unusable request: ``_IPCHandler.handle`` returns before writing whenever
    the payload does not decode. The probe is therefore answered exactly as
    before, and merely stops being recorded as a fault.

    Apply this alongside :func:`_enable_cmd_mox_ipc_disconnect_tolerance`. The
    two are separate because they are separate connections: this one sends
    nothing and never reaches the reply, while that one sends a valid request
    and reaches the reply after the client has gone. Both front the same
    upstream defect -- the empty read is the second failure mode of
    `cmd-mox#256`_ -- and both disappear when ``leynos/cmd-mox#259`` lands and
    the pin advances; see the sibling guard for that boundary.

    .. _cmd-mox#256: https://github.com/leynos/cmd-mox/issues/256
    """
    if sys.platform == "win32":  # pragma: no cover - cmd-mox unavailable
        return
    try:
        from cmd_mox.ipc import server as ipc_server
    except (ModuleNotFoundError, ImportError):  # pragma: no cover - private API
        return

    parse = getattr(ipc_server, "_parse_payload", None)
    if parse is None or getattr(parse, "__cmd_mox_empty_probe_guard__", False):
        return

    def parse_without_a_probe_fault(
        raw: bytes,
    ) -> tuple[dict[str, object], str] | None:
        # Zero bytes is the readiness probe, not malformed input.
        if not raw:
            return None
        return original_parse(raw)

    original_parse = parse
    parse_without_a_probe_fault.__cmd_mox_empty_probe_guard__ = True
    # Kept reachable so a regression test can put the unguarded parser back and
    # show the traceback really is what the guard prevents, rather than
    # asserting a silence that was never at risk.
    parse_without_a_probe_fault.__cmd_mox_unguarded__ = original_parse
    ipc_server._parse_payload = parse_without_a_probe_fault


@pytest.fixture(autouse=True, scope="session")
def _cmd_mox_empty_probe_tolerance() -> None:
    """Apply the cmd-mox readiness-probe guard once per test session."""
    _enable_cmd_mox_empty_probe_tolerance()


@pytest.fixture(autouse=True, scope="session")
def _cmd_mox_replay_idempotence() -> None:
    """Apply cmd-mox replay compatibility patch once per test session."""
    _enable_cmd_mox_replay_idempotence()


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
