"""Tests for the Windows standard-stream configuration in ``run_rust``.

The ``run_rust`` script forces UTF-8 on ``sys.stdout`` and ``sys.stderr`` when
running on Windows, preferring in-place reconfiguration and falling back to
wrapping the underlying buffer. These tests exercise the helpers directly on
every platform because the behaviour is pure stream plumbing.
"""

from __future__ import annotations

import io
import logging
import sys
import typing as typ

import pytest
from script_loader import load_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


@pytest.fixture
def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module for testing."""
    return load_script_module(monkeypatch, "run_rust")


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
    "error",
    [
        AttributeError("no attr"),
        ValueError("bad value"),
        io.UnsupportedOperation("unsupported"),
        OSError("io failure"),
    ],
    ids=["attribute-error", "value-error", "unsupported-operation", "os-error"],
)
def test_try_reconfigure_windows_stream_handles_failures(
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Handled reconfiguration failures are reported without propagating."""
    caplog.set_level(logging.DEBUG)
    stream = _ReconfigurableStream(error=error)

    assert not run_rust_module._try_reconfigure_windows_stream(stream, "stdout", "1")
    assert "Failed to reconfigure stdout" in caplog.text


def test_try_reconfigure_windows_stream_failure_is_quiet_without_debug(
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No diagnostic is emitted when ``RUN_RUST_DEBUG`` is unset."""
    caplog.set_level(logging.DEBUG)
    stream = _ReconfigurableStream(error=OSError("io failure"))

    assert not run_rust_module._try_reconfigure_windows_stream(stream, "stdout", None)
    assert caplog.text == ""


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


def test_try_wrap_windows_stream_wrapping_failure(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A buffer that cannot be wrapped leaves the stream untouched."""
    caplog.set_level(logging.DEBUG)
    original = sys.stderr
    monkeypatch.setattr(sys, "stderr", original)
    closed = io.BytesIO()
    closed.close()

    run_rust_module._try_wrap_windows_stream(_PlainStream(closed), "stderr", "1")

    assert sys.stderr is original
    assert "Failed to wrap stderr" in caplog.text


def test_try_wrap_windows_stream_failure_is_quiet_without_debug(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wrapping failures stay silent when ``RUN_RUST_DEBUG`` is unset."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    closed = io.BytesIO()
    closed.close()

    run_rust_module._try_wrap_windows_stream(_PlainStream(closed), "stderr", None)

    assert caplog.text == ""


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
