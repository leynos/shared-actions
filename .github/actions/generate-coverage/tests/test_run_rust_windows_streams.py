"""Tests for the Windows standard-stream configuration in ``run_rust``.

The ``run_rust`` script forces UTF-8 on ``sys.stdout`` and ``sys.stderr`` when
running on Windows, preferring in-place reconfiguration and falling back to
wrapping the underlying buffer. These tests exercise the helpers directly on
every platform because the behaviour is pure stream plumbing.
"""

from __future__ import annotations

import gc
import io
import logging
import sys
import types
import typing as typ

import pytest
from script_loader import load_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


@pytest.fixture
def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded Windows stream helper module for testing."""
    return load_script_module(monkeypatch, "windows_standard_streams")


class _ReconfigurableStream:
    """Stream test double recording ``reconfigure`` keyword arguments."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.buffer = io.BytesIO()

    def reconfigure(self, **kwargs: object) -> None:
        """Record the call, raising ``self.error`` when configured to fail."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class _PlainStream:
    """Stream test double without ``reconfigure``, with a settable buffer."""

    def __init__(self, buffer: object | None = None) -> None:
        if buffer is not None:
            self.buffer = buffer


class _FailsOnFlush(io.BytesIO):
    """Byte buffer that raises once on a selected flush call."""

    def __init__(self, failing_call: int) -> None:
        super().__init__()
        self.failing_call = failing_call
        self.flush_calls = 0
        self.error = OSError("simulated flush failure")

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_calls == self.failing_call:
            raise self.error
        super().flush()


class _FailsFromFlush(io.BytesIO):
    """Byte buffer that keeps failing after a selected flush call."""

    def __init__(self, first_failing_call: int) -> None:
        super().__init__()
        self.first_failing_call = first_failing_call
        self.flush_calls = 0
        self.fail = True
        self.error = OSError("simulated persistent flush failure")

    def flush(self) -> None:
        self.flush_calls += 1
        if self.fail and self.flush_calls >= self.first_failing_call:
            raise self.error
        super().flush()


def test_try_reconfigure_windows_stream_success(
    run_rust_module: ModuleType,
) -> None:
    """A reconfigurable stream is switched to UTF-8 and reported as handled."""
    stream = _ReconfigurableStream()

    assert run_rust_module._try_reconfigure_windows_stream(stream, "stdout", None)
    assert stream.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_try_reconfigure_windows_stream_without_reconfigure(
    run_rust_module: ModuleType,
) -> None:
    """A stream lacking ``reconfigure`` reports that it was not handled."""
    assert not run_rust_module._try_reconfigure_windows_stream(
        _PlainStream(), "stdout", None
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            (AttributeError("no attr"), "1", "Failed to reconfigure stdout"),
            id="attribute-error-debugged",
        ),
        pytest.param(
            (ValueError("bad value"), "1", "Failed to reconfigure stdout"),
            id="value-error-debugged",
        ),
        pytest.param(
            (
                io.UnsupportedOperation("unsupported"),
                "1",
                "Failed to reconfigure stdout",
            ),
            id="unsupported-operation-debugged",
        ),
        pytest.param(
            (OSError("io failure"), "1", "Failed to reconfigure stdout"),
            id="os-error-debugged",
        ),
        pytest.param((OSError("io failure"), None, None), id="os-error-without-debug"),
    ],
)
def test_try_reconfigure_windows_stream_handles_failures(
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
    case: tuple[Exception, str | None, str | None],
) -> None:
    """Reconfiguration failures stay contained and log only in debug mode."""
    error, debug, expected_log = case
    caplog.set_level(logging.DEBUG)
    stream = _ReconfigurableStream(error=error)

    assert not run_rust_module._try_reconfigure_windows_stream(stream, "stdout", debug)
    if expected_log is None:
        assert caplog.text == ""
    else:
        assert expected_log in caplog.text


def test_try_wrap_windows_stream_wraps_buffer(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
) -> None:
    """The stream buffer is rebound on ``sys`` as a UTF-8 text wrapper."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    stream = _PlainStream(io.BytesIO())

    run_rust_module._try_wrap_windows_stream(stream, "stdout", None)

    wrapped = sys.stdout
    assert isinstance(wrapped, io.TextIOWrapper)
    assert wrapped.encoding == "utf-8"
    assert wrapped.errors == "replace"
    assert wrapped.write_through


def test_try_wrap_windows_stream_releases_original_buffer_ownership(
    run_rust_module: ModuleType,
) -> None:
    """The replacement remains usable after the original wrapper is collected."""
    original_stdout = sys.stdout
    buffer = io.BytesIO()
    old_wrapper = io.TextIOWrapper(buffer, encoding="utf-8")
    try:
        sys.stdout = old_wrapper
        run_rust_module._try_wrap_windows_stream(old_wrapper, "stdout", None)
        replacement = sys.stdout
        sys.stdout = original_stdout
        del old_wrapper
        gc.collect()

        assert not buffer.closed
        replacement.write("still open")
        replacement.flush()
        assert buffer.getvalue() == b"still open"
    finally:
        sys.stdout = original_stdout


@pytest.mark.parametrize("failing_call", [1, 2], ids=["preflight", "detach"])
def test_try_wrap_windows_stream_preserves_stream_when_flush_fails(
    run_rust_module: ModuleType,
    failing_call: int,
) -> None:
    """A failed flush leaves the original stream and buffer usable."""
    original_stdout = sys.stdout
    buffer = _FailsOnFlush(failing_call)
    old_wrapper = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
    try:
        old_wrapper.write("pending output")
        sys.stdout = old_wrapper
        run_rust_module._try_wrap_windows_stream(old_wrapper, "stdout", None)

        assert sys.stdout is old_wrapper
        assert not buffer.closed
        assert old_wrapper.buffer is buffer
    finally:
        sys.stdout = original_stdout
        old_wrapper.close()


def test_failed_detach_does_not_close_the_shared_buffer(
    run_rust_module: ModuleType,
) -> None:
    """Persistent detach failure leaves sole ownership with the original."""
    original_stdout = sys.stdout
    buffer = _FailsFromFlush(first_failing_call=2)
    old_wrapper = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
    try:
        old_wrapper.write("pending output")
        sys.stdout = old_wrapper
        run_rust_module._try_wrap_windows_stream(old_wrapper, "stdout", None)
        gc.collect()

        assert sys.stdout is old_wrapper
        assert not buffer.closed
        assert old_wrapper.buffer is buffer
    finally:
        sys.stdout = original_stdout
        buffer.fail = False
        old_wrapper.close()


def test_importing_run_rust_does_not_configure_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading the coverage module leaves process-global streams untouched."""
    calls: list[str] = []
    stream_module = types.ModuleType("windows_standard_streams")
    monkeypatch.setattr(
        stream_module,
        "_configure_windows_standard_streams_on_windows",
        lambda: calls.append("configured"),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "windows_standard_streams", stream_module)

    load_script_module(monkeypatch, "run_rust")

    assert calls == []


def test_try_wrap_windows_stream_without_buffer(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stream without a buffer is left alone and reported when debugging."""
    caplog.set_level(logging.DEBUG)
    original = sys.stdout
    monkeypatch.setattr(sys, "stdout", original)

    run_rust_module._try_wrap_windows_stream(_PlainStream(), "stdout", "1")

    assert sys.stdout is original
    assert "stdout has no buffer; leaving as-is" in caplog.text


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("1", "Failed to wrap stderr"), id="debug-enabled"),
        pytest.param((None, None), id="debug-unset"),
    ],
)
def test_try_wrap_windows_stream_wrapping_failure(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
    case: tuple[str | None, str | None],
) -> None:
    """A failed wrap leaves the stream untouched and logs only in debug mode."""
    debug, expected_log = case
    caplog.set_level(logging.DEBUG)
    original = sys.stderr
    monkeypatch.setattr(sys, "stderr", original)
    closed = io.BytesIO()
    closed.close()

    run_rust_module._try_wrap_windows_stream(_PlainStream(closed), "stderr", debug)

    assert sys.stderr is original
    if expected_log is None:
        assert caplog.text == ""
    else:
        assert expected_log in caplog.text


def test_configure_windows_standard_streams_prefers_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
) -> None:
    """Both standard streams are reconfigured in place when supported."""
    streams = {name: _ReconfigurableStream() for name in ("stdout", "stderr")}
    for name, stream in streams.items():
        monkeypatch.setattr(sys, name, stream)

    run_rust_module._configure_windows_standard_streams()

    for name, stream in streams.items():
        assert stream.calls == [{"encoding": "utf-8", "errors": "replace"}]
        assert getattr(sys, name) is stream


def test_configure_windows_standard_streams_falls_back_to_wrapping(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
) -> None:
    """Streams that reject reconfiguration are wrapped around their buffer."""
    for name in ("stdout", "stderr"):
        monkeypatch.setattr(
            sys, name, _ReconfigurableStream(error=io.UnsupportedOperation(name))
        )

    run_rust_module._configure_windows_standard_streams()

    for name in ("stdout", "stderr"):
        wrapped = getattr(sys, name)
        assert isinstance(wrapped, io.TextIOWrapper)
        assert wrapped.encoding == "utf-8"


def test_log_windows_stream_debug_respects_flag(
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug diagnostics are emitted only when the debug flag is set."""
    caplog.set_level(logging.DEBUG)

    run_rust_module._log_windows_stream_debug(None, "quiet %s", "stdout")
    assert caplog.text == ""

    run_rust_module._log_windows_stream_debug("1", "loud %s", "stdout")
    assert "loud stdout" in caplog.text
