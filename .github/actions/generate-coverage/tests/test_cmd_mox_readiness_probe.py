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

The fault count is asserted by calling the parser directly rather than by
driving a probe through a socket. A socket probe would be more end-to-end, but
the record it produces lands on a logger shared by every connection in the
process, so counting records over any window measures the neighbours as well
as the probe. Under ``--dist worksteal`` the neighbours differ between runs,
which is how an earlier socket-driven version of this test failed
intermittently rather than on every run. See :class:`_ThreadRecordCounter`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import threading
import typing as typ

import pytest

# Private attribute, patched by the session fixture in conftest.py. Guarded
# because cmd-mox is a pinned dependency whose module layout is not ours to
# rely on at import time.
_IPC_SERVER = pytest.importorskip("cmd_mox.ipc.server")

_SOCKET_ENV = "CMOX_IPC_SOCKET"

#: The logger cmd-mox reports a malformed read against.
_SERVER_LOGGER = "cmd_mox.ipc.server"

#: The record the guard exists to suppress, matched against the message so a
#: passing test cannot pass on an unrelated error.
_MALFORMED = "malformed JSON"

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


def _read_reply(client: socket.socket) -> bytes:
    """Read until EOF, so a reply split across receives is still complete.

    ``recv`` returns what has arrived, not everything the server sent, so a
    single call can return part of the reply. The server closes the
    connection once the handler returns, which is the framing: read until
    the close.
    """
    chunks: list[bytes] = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


class _ThreadRecordCounter(logging.Handler):
    """Count the records one thread causes a logger to emit.

    ``Logger.addHandler`` is process-global: while this handler is
    attached, a record from *any* thread reaches it. Filtering by thread is
    what makes the count attributable, because the records that matter are
    emitted synchronously by the thread that calls the parser. Records
    from connections the server happens to be serving at the same time are
    emitted by their own handler threads and are not counted.
    """

    def __init__(self, owner: int) -> None:
        super().__init__()
        self._owner = owner
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep *record* when the owning thread is the one that emitted it."""
        if record.thread == self._owner:
            self.records.append(record)


@contextlib.contextmanager
def _counting_records() -> typ.Iterator[list[logging.LogRecord]]:
    """Collect the server logger's records for the duration of the block."""
    logger = logging.getLogger(_SERVER_LOGGER)
    counter = _ThreadRecordCounter(threading.get_ident())
    previous_level = logger.level
    logger.addHandler(counter)
    logger.setLevel(logging.ERROR)
    try:
        yield counter.records
    finally:
        logger.removeHandler(counter)
        logger.setLevel(previous_level)


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


def test_an_empty_probe_leaves_no_server_traceback(cmd_mox: object) -> None:
    """A readiness probe produces no error record.

    The unguarded parser is exercised first, so this fails if the guard is
    removed rather than merely asserting a silence that was never at risk.
    Both calls are direct and synchronous, which is what lets the record
    count be attributed to the read rather than to whatever else the
    process happened to be serving.
    """
    guarded = _IPC_SERVER._parse_payload
    unguarded = guarded.__cmd_mox_unguarded__  # type: ignore[attr-defined]

    with _counting_records() as records:
        assert unguarded(b"") is None, "the unguarded parser changed its answer"

    assert len(records) == 1, (
        f"the unguarded parser emitted {len(records)} error records for an "
        "empty read, not the one malformed-JSON record the guard exists to "
        "suppress; the guard is not what is silencing it"
    )
    assert _MALFORMED in records[0].getMessage(), (
        f"the unguarded parser's record is {records[0].getMessage()!r}, not the "
        "malformed-JSON record the probe is being conflated with"
    )

    with _counting_records() as records:
        assert guarded(b"") is None, "the guard stopped returning None"

    assert records == [], (
        f"an empty read left {len(records)} error records, so the guard is not "
        "absorbing it"
    )


def test_a_probe_does_not_upset_the_next_request(cmd_mox: object) -> None:
    """The shim still gets its answer after the readiness check has run.

    The observable contract: a real request that follows a probe is answered
    normally. This deliberately makes no claim about what the probe logged,
    which is :func:`test_an_empty_probe_leaves_no_server_traceback`'s job --
    asserting it here as well would only re-introduce a count over a shared
    logger.
    """
    cmd_mox.stub("cargo").returns(stdout="Coverage: 81.5%\n")  # type: ignore[attr-defined]
    cmd_mox.replay()  # type: ignore[attr-defined]
    socket_path = os.environ[_SOCKET_ENV]

    _probe(socket_path)

    client = _connect(socket_path)
    try:
        client.sendall(json.dumps(_REQUEST).encode())
        client.shutdown(socket.SHUT_WR)
        reply = _read_reply(client)
    finally:
        client.close()

    assert reply, "the real request produced no reply"
    assert json.loads(reply)["exit_code"] == 0
