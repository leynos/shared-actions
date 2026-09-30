"""Regression coverage for the cmd-mox readiness-probe guard.

``cmd_mox.ipc.socket_utils._try_socket_connection`` -- and the probe inside
``cleanup_stale_socket`` -- connect to the socket and close without writing a
byte. The only question either asks is whether something is listening. The
server then reads zero bytes, ``json.loads(b"")`` raises ``JSONDecodeError``,
and ``_parse_payload`` records the expected handshake as malformed input, with
a full traceback, once per server start.

That empty read is the whole signal: a client with a request to make always
sends one. The root ``conftest.py`` turns the empty read into the ``None`` the
server already returns for every unusable payload, so the probe is answered as
before and merely stops being logged as a fault.

This is a different connection from the one the disconnect guard covers. That
guard wraps the reply writer, which only a valid request reaches; this one
covers the read that never gets that far. Keeping the two in separate files
keeps that distinction visible.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time

import pytest

# Private attribute, patched by the session fixture in conftest.py. Guarded
# because cmd-mox is a pinned dependency whose module layout is not ours to
# rely on at import time.
_IPC_SERVER = pytest.importorskip("cmd_mox.ipc.server")

_SOCKET_ENV = "CMOX_IPC_SOCKET"
_REQUEST = {
    "kind": "invocation",
    "command": "cargo",
    "args": ["--version"],
    "stdin": "",
    "env": {},
    "invocation_id": "probe-guard",
}


def _connect(socket_path: str) -> socket.socket:
    """Return a connected client socket with a bounded timeout."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(socket_path)
    return client


def _probe(socket_path: str) -> None:
    """Connect and close sending nothing, exactly as the readiness check does."""
    _connect(socket_path).close()


def test_the_guard_is_installed_by_the_session_fixture(cmd_mox: object) -> None:
    """The suite wires the guard in rather than leaving the parse unguarded."""
    parse = _IPC_SERVER._parse_payload

    assert getattr(parse, "__cmd_mox_empty_probe_guard__", False)
    assert getattr(parse, "__cmd_mox_unguarded__", None) is not None


def test_empty_read_yields_no_payload(cmd_mox: object) -> None:
    """The probe still gets the ``None`` answer it has always received."""
    assert _IPC_SERVER._parse_payload(b"") is None


def test_a_real_request_is_still_parsed(cmd_mox: object) -> None:
    """The guard intercepts the empty read only, not a genuine payload."""
    parsed = _IPC_SERVER._parse_payload(json.dumps(_REQUEST).encode())

    assert parsed is not None
    payload, kind = parsed
    assert kind == "invocation"
    assert payload["command"] == "cargo"


def test_an_empty_probe_leaves_no_server_traceback(
    cmd_mox: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A readiness probe produces no error record.

    The unguarded parser is exercised first, so this fails if the guard is
    removed rather than merely asserting a silence that was never at risk.
    """
    cmd_mox.stub("cargo").returns(stdout="Coverage: 81.5%\n")  # type: ignore[attr-defined]
    cmd_mox.replay()  # type: ignore[attr-defined]
    guarded = _IPC_SERVER._parse_payload
    socket_path = os.environ[_SOCKET_ENV]

    try:
        _IPC_SERVER._parse_payload = guarded.__cmd_mox_unguarded__
        with caplog.at_level(logging.ERROR, logger="cmd_mox.ipc.server"):
            _probe(socket_path)
            time.sleep(0.8)
        assert "malformed JSON" in caplog.text
    finally:
        _IPC_SERVER._parse_payload = guarded

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="cmd_mox.ipc.server"):
        _probe(socket_path)
        time.sleep(0.8)

    assert caplog.text == ""


def test_a_probe_does_not_upset_the_next_request(cmd_mox: object) -> None:
    """The shim still gets its answer after the readiness check has run."""
    cmd_mox.stub("cargo").returns(stdout="Coverage: 81.5%\n")  # type: ignore[attr-defined]
    cmd_mox.replay()  # type: ignore[attr-defined]
    socket_path = os.environ[_SOCKET_ENV]

    _probe(socket_path)
    time.sleep(0.4)

    client = _connect(socket_path)
    try:
        client.sendall(json.dumps(_REQUEST).encode())
        client.shutdown(socket.SHUT_WR)
        reply = client.recv(65536)
    finally:
        client.close()

    assert reply, "the real request produced no reply"
    assert json.loads(reply)["exit_code"] == 0
