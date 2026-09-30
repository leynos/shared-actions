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
import time
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
_REQUEST = {
    "kind": "invocation",
    "command": "cargo",
    "args": ["--version"],
    "stdin": "",
    "env": {},
    "invocation_id": "disconnect-guard",
}


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
        _abandon_a_request(socket_path)
        time.sleep(0.8)
        assert "BrokenPipeError" in capfd.readouterr().err
    finally:
        handler.setup = guarded_setup

    _abandon_a_request(socket_path)
    time.sleep(0.8)

    assert "Traceback" not in capfd.readouterr().err


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
    # returned before the write. A subsequent real request must still work.
    time.sleep(0.4)
    shim = Path(os.environ["PATH"].split(os.pathsep)[0]) / "cargo"
    result = run_plumbum_command(
        local[str(shim)]["--version"],
        method="run",
        env=dict(os.environ),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Coverage: 81.5%\n"
