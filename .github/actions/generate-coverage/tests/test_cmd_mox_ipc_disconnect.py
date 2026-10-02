"""Regression coverage for the cmd-mox IPC disconnect guard.

``_IPCHandler.handle`` writes the reply to ``self.wfile`` with no guard. When
the client has already gone, that write raises and escapes the handler, and
``socketserver.ThreadingMixIn.process_request_thread`` prints a full
server-side traceback. The root ``conftest.py`` installs a writer that treats a
departed peer as an empty write.

The end-to-end test below is deterministic because an abruptly closed socket
whose receive queue still holds the unread reply is reset by the peer's kernel,
so the server's write reliably fails. That is the same condition CI hit.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
import socketserver
import threading
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from shared_actions_conftest import _DisconnectTolerantWriter

from test_support.plumbum_helpers import run_plumbum_command

# The guard is applied to this private attribute by the session fixture in
# conftest.py. Guarded because cmd-mox is a pinned dependency whose module
# layout is not ours to rely on at import time.
_IPC_SERVER = pytest.importorskip("cmd_mox.ipc.server")

_SOCKET_ENV = "CMOX_IPC_SOCKET"

#: How long to wait for the server to finish with a request before failing
#: the test. Generous because it is only ever reached in full when
#: something is wrong; the happy path clears it in milliseconds.
_HANDLED: typ.Final[float] = 5.0

#: Names the connection under test. This test's request is the only one
#: carrying it, so the thread that parses these bytes is the one to wait
#: for -- see :class:`_ThreadFinished`. A byte string because it is matched
#: against the raw read, which is bytes; `str in bytes` raises.
_MARKER = b"disconnect-guard"

_REQUEST = {
    "kind": "invocation",
    "command": "cargo",
    "args": ["--version"],
    "stdin": "",
    "env": {},
    "invocation_id": _MARKER.decode(),
}


class _ThreadFinished:
    """Completion signal for the thread that serves one specific request.

    The signal must be about *this* connection. A wait on the server as a
    whole is not: a readiness probe the shim makes on its own initiative
    is served by its own thread, so "some request finished" can be true
    before the request just sent has been handled at all. An earlier
    version of this test waited on that and passed or failed on timing.

    The connection is identified by the request it carries. This test's
    request is the only one that names :data:`_MARKER`, so flagging the
    thread that parses those bytes and then signalling when *that* thread
    ends is what makes the following assertion attributable. The thread
    that parses is the thread that writes the reply and the thread whose
    ``handle_error`` prints, so its ending is the last thing to wait for.
    """

    __slots__ = ("_done", "_flagged", "_lock", "_originals")

    def __init__(self) -> None:
        self._done = threading.Event()
        self._flagged: set[int] = set()
        self._lock = threading.Lock()
        self._originals: dict[str, typ.Callable[..., object]] = {}

    @classmethod
    def watching(cls) -> typ.ContextManager[_ThreadFinished]:
        """Return a context that signals the thread serving the marked request."""
        return cls()

    def __enter__(self) -> _ThreadFinished:
        """Patch the parse and thread entry points, and return this signal."""
        signal = self
        process = _IPC_SERVER._process_raw_request
        thread_entry = socketserver.ThreadingMixIn.process_request_thread
        self._originals = {"process": process, "thread": thread_entry}

        def process_with_mark(server: object, raw: bytes) -> object:
            if _MARKER in raw:
                with signal._lock:
                    signal._flagged.add(threading.get_ident())
            return process(server, raw)

        def thread_with_signal(
            server: object,
            request: object,
            client_address: object,
        ) -> None:
            try:
                thread_entry(server, request, client_address)
            finally:
                # Set in a `finally` because the case under test is the
                # one where the write raises and the thread ends through
                # the error path that prints the traceback.
                with signal._lock:
                    mine = threading.get_ident() in signal._flagged
                if mine:
                    signal._done.set()

        _IPC_SERVER._process_raw_request = process_with_mark
        _IPC_SERVER._InnerServer.process_request_thread = thread_with_signal
        return self

    def __exit__(self, *_exc: object) -> None:
        """Put the originals back."""
        if self._originals:
            _IPC_SERVER._process_raw_request = self._originals["process"]
            _IPC_SERVER._InnerServer.process_request_thread = self._originals["thread"]

    def done(self, *, what: str) -> None:
        """Return once the marked request has been served, or fail saying so."""
        assert self._done.wait(_HANDLED), (
            f"the server never finished {what}; nothing was waiting on a timer "
            "to hide it, so this is the whole timeout"
        )
        # Clear so a later `done()` waits for the next marked request
        # rather than returning on this one's signal.
        self._done.clear()


def _stderr(capfd: pytest.CaptureFixture[str]) -> str:
    """Return everything written to stderr so far, and forget it.

    `readouterr` consumes what it returns, so each test reads once, after
    the wait that makes the read meaningful. Reading twice would leave the
    second call empty whatever the server had done in between.
    """
    return capfd.readouterr().err


def _abandon_a_request(socket_path: str) -> None:
    """Send a valid request, then close without waiting for the reply."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(socket_path)
        client.sendall(json.dumps(_REQUEST).encode())
    finally:
        # Closing with the reply still queued resets the connection, which is
        # exactly the "client gave up and went away" case under test.
        client.close()


class _AlwaysFails:
    """Stand-in writer that raises *error* on every write."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def write(self, data: bytes) -> int:
        """Raise the configured error."""
        raise self._error


class _Recorder:
    """Stand-in writer that accumulates written bytes."""

    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> int:
        """Record *data* and report it as fully written."""
        self.data += data
        return len(data)


def test_departed_client_is_an_empty_write() -> None:
    """A peer that has gone turns the reply write into a no-op."""
    writer = _DisconnectTolerantWriter(_AlwaysFails(BrokenPipeError(32, "Broken pipe")))

    assert writer.write(b"payload") == 0


def test_aborted_connection_is_an_empty_write() -> None:
    """A reset connection is treated the same as a broken pipe."""
    writer = _DisconnectTolerantWriter(
        _AlwaysFails(ConnectionResetError(104, "Connection reset by peer"))
    )

    assert writer.write(b"payload") == 0


def test_local_write_faults_still_propagate() -> None:
    """A genuine local fault is not mistaken for a vanished client."""
    writer = _DisconnectTolerantWriter(
        _AlwaysFails(OSError(errno.ENOSPC, "No space left on device"))
    )

    with pytest.raises(OSError):  # noqa: PT011 - any OSError is the contract
        writer.write(b"payload")


def test_connected_client_payload_passes_through() -> None:
    """The guard is transparent while the client is still listening."""
    sink = _Recorder()
    writer = _DisconnectTolerantWriter(sink)

    assert writer.write(b"abc") == 3
    assert sink.data == b"abc"


def test_guard_forwards_other_stream_attributes() -> None:
    """Attribute access other than ``write`` reaches the wrapped stream."""

    class _Stream:
        flushed = False

        def flush(self) -> None:
            self.flushed = True

    stream = _Stream()
    _DisconnectTolerantWriter(stream).flush()

    assert stream.flushed


def test_abandoned_client_does_not_print_a_server_traceback(
    cmd_mox: object,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """An abandoned request leaves no traceback on the server's stderr.

    The unguarded path is exercised first so the test fails if the guard is
    removed rather than merely asserting a silence that was never at risk.
    """
    cmd_mox.stub("cargo").returns(stdout="Coverage: 81.5%\n")  # type: ignore[attr-defined]
    cmd_mox.replay()  # type: ignore[attr-defined]
    socket_path = os.environ[_SOCKET_ENV]

    handler = _IPC_SERVER._IPCHandler
    guarded_setup = handler.setup

    def unguarded_setup(self: object) -> None:
        # The upstream implementation: attach the raw writer with no guard.
        socketserver.StreamRequestHandler.setup(self)  # type: ignore[arg-type]

    try:
        handler.setup = unguarded_setup
        with _ThreadFinished.watching() as watched:
            _abandon_a_request(socket_path)
            watched.done(what="the unguarded request")
        # The thread has ended, so `handle_error` has already printed
        # whatever it was going to print for this connection. Which of the
        # two disconnect errors surfaces depends on kernel timing: a write to
        # a socket whose peer has gone raises BrokenPipeError or
        # ConnectionResetError, and the guard treats both as a departed
        # client (see `test_aborted_connection_is_an_empty_write`). Assert
        # that the unguarded path raised at all rather than pinning the race
        # to one name, and carry the stderr so a failure shows what it saw.
        stderr = _stderr(capfd)
        assert "BrokenPipeError" in stderr or "ConnectionResetError" in stderr, (
            f"the unguarded write did not raise a disconnect error:\n{stderr}"
        )
    finally:
        handler.setup = guarded_setup

    with _ThreadFinished.watching() as watched:
        _abandon_a_request(socket_path)
        watched.done(what="the guarded request")

    assert "Traceback" not in _stderr(capfd)


def test_guard_is_installed_by_the_session_fixture(cmd_mox: object) -> None:
    """The suite wires the guard in rather than leaving the shim unguarded."""
    handler = _IPC_SERVER._IPCHandler

    assert getattr(handler.setup, "__cmd_mox_disconnect_guard__", False)


def test_readiness_probe_still_returns_no_reply(cmd_mox: object) -> None:
    """The bare connect/close probe keeps taking the silent server path.

    ``cmd_mox.ipc.socket_utils._try_socket_connection`` connects and closes
    without sending anything. ``_parse_payload`` reads zero bytes, and
    ``_IPCHandler.handle`` returns before the write, so the guard never sees
    this connection. Holding that contract here keeps the two behaviours from
    being conflated again.
    """
    cmd_mox.stub("cargo").returns(stdout="Coverage: 81.5%\n")  # type: ignore[attr-defined]
    cmd_mox.replay()  # type: ignore[attr-defined]

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(5)
    with contextlib.closing(probe):
        probe.connect(os.environ[_SOCKET_ENV])

    # Nothing was sent, so there is nothing to read back and the handler
    # returns before the write. No wait is needed between the probe and the
    # request below: they are different connections, served in their own
    # threads, and the reply this test asserts on is read by blocking on
    # `recv`, so it is synchronised by the contract itself rather than by a
    # timer. An earlier draft waited here anyway, on a server-wide event that
    # the probe's own thread satisfied.
    shim = Path(os.environ["PATH"].split(os.pathsep)[0]) / "cargo"
    result = run_plumbum_command(
        local[str(shim)]["--version"],
        method="run",
        env=dict(os.environ),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Coverage: 81.5%\n"
