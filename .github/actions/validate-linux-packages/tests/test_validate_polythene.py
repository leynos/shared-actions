"""Tests for polythene sandbox helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from plumbum.commands.processes import ProcessExecutionError

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_polythene.py"


@pytest.fixture(scope="module")
def validate_polythene_module() -> object:
    """Load the validate_polythene module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_polythene")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_polythene", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_polythene module"
        raise RuntimeError(message)

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeCommand:
    def __init__(self, argv: tuple[str, ...], calls: list[tuple[str, ...]]) -> None:
        self.argv = argv
        self._calls = calls

    def __call__(self) -> str:
        self._calls.append(self.argv)
        return ""

    def __getitem__(self, value: str | tuple[str, ...]) -> _FakeCommand:
        if not isinstance(value, tuple):
            value = (value,)
        return _FakeCommand(self.argv + value, self._calls)


class _FakeLocal:
    def __init__(self, calls: list[tuple[str, ...]]) -> None:
        self._calls = calls

    def __getitem__(self, value: str | tuple[str, ...]) -> _FakeCommand:
        if not isinstance(value, tuple):
            value = (value,)
        return _FakeCommand(value, self._calls)


def test_default_polythene_command_uses_module(
    validate_polythene_module: object,
) -> None:
    """default_polythene_command invokes the installed package."""
    command = validate_polythene_module.default_polythene_command()

    assert command == ("polythene",)


def test_polythene_session_exec_respects_timeouts(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PolytheneSession.exec forwards timeout values to run_text."""
    calls: list[tuple[tuple[str, ...], int | None]] = []

    def _capture(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        calls.append((argv, timeout))
        return "ok"

    monkeypatch.setattr(validate_polythene_module, "run_text", _capture)
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))

    session = validate_polythene_module.PolytheneSession(
        ("polythene",),
        "sandbox-uid",
        tmp_path,
        timeout=30,
        isolation="proot",
    )

    session.exec("echo", "hello")
    session.exec("echo", "world", timeout=5)

    assert calls[0][1] == 30
    assert calls[1][1] == 5
    argv = calls[0][0]
    assert "--isolation" in argv
    assert argv[argv.index("--isolation") + 1] == "proot"


def test_polythene_session_exec_omits_isolation_when_unset(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PolytheneSession.exec does not inject --isolation when unset."""
    calls: list[tuple[tuple[str, ...], int | None]] = []

    def _capture(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        calls.append((argv, timeout))
        return "ok"

    monkeypatch.setattr(validate_polythene_module, "run_text", _capture)
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))

    session = validate_polythene_module.PolytheneSession(
        ("polythene",),
        "sandbox-uid",
        tmp_path,
    )

    session.exec("echo", "hello")

    argv = calls[0][0]
    assert "--isolation" not in argv


def test_polythene_rootfs_yields_configured_session(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """polythene_rootfs provisions the sandbox and cleans up."""
    run_calls: list[tuple[tuple[str, ...], int | None]] = []
    cleanup_calls: list[tuple[str, ...]] = []

    def _run_text(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        run_calls.append((argv, timeout))
        return "session-uid\n" if "pull" in argv else ""

    monkeypatch.setattr(validate_polythene_module, "run_text", _run_text)
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal(cleanup_calls))

    store = tmp_path / "store"
    polythene = tmp_path / "polythene.py"
    polythene.write_text("#!/usr/bin/env python\n")
    command = (polythene.as_posix(),)

    monkeypatch.delenv("POLYTHENE_ISOLATION", raising=False)

    with validate_polythene_module.polythene_rootfs(
        command,
        "docker.io/library/debian:bookworm",
        store,
        timeout=12,
    ) as session:
        assert session.command == command
        assert session.uid == "session-uid"
        assert session.store == store
        assert session.root.exists()
        assert session.timeout == 12
        assert session.isolation == validate_polythene_module.DEFAULT_ISOLATION

    assert cleanup_calls
    cleanup_argv = cleanup_calls[0]
    assert cleanup_argv[:4] == ("uv", "run", polythene.as_posix(), "rm")
    assert run_calls[0][1] == 12  # pull timeout
    assert run_calls[1][1] == 12  # initial exec timeout
    exec_argv = run_calls[1][0]
    assert "--isolation" in exec_argv
    assert exec_argv[exec_argv.index("--isolation") + 1] == "proot"


def test_polythene_rootfs_honours_environment_isolation_override(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Environment-provided POLYTHENE_ISOLATION overrides the default value."""
    run_calls: list[tuple[tuple[str, ...], int | None]] = []

    def _run_text(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        run_calls.append((argv, timeout))
        return "session-uid\n" if "pull" in argv else ""

    monkeypatch.setattr(validate_polythene_module, "run_text", _run_text)
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))
    monkeypatch.setenv("POLYTHENE_ISOLATION", "chroot")

    store = tmp_path / "store"
    polythene = tmp_path / "polythene.py"
    polythene.write_text("#!/usr/bin/env python\n")
    command = (polythene.as_posix(),)

    with validate_polythene_module.polythene_rootfs(
        command,
        "docker.io/library/debian:bookworm",
        store,
    ) as session:
        assert session.isolation == "chroot"

    exec_argv = run_calls[1][0]
    assert "--isolation" in exec_argv
    assert exec_argv[exec_argv.index("--isolation") + 1] == "chroot"


def test_polythene_rootfs_rejects_empty_identifier(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty identifier from polythene pull raises ValidationError."""
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))

    def _pull_empty(command: object, *, timeout: int | None = None) -> str:
        return "\n"

    monkeypatch.setattr(validate_polythene_module, "run_text", _pull_empty)

    store = tmp_path / "store"
    polythene = tmp_path / "polythene.py"
    polythene.write_text("#!/usr/bin/env python\n")
    command = (polythene.as_posix(),)

    with (
        pytest.raises(validate_polythene_module.ValidationError),
        validate_polythene_module.polythene_rootfs(
            command,
            "docker.io/library/debian:bookworm",
            store,
        ),
    ):
        pass


def test_decode_stream_normalises_values(
    validate_polythene_module: object,
) -> None:
    """_decode_stream converts bytes, None and other values to text."""
    decode_stream = validate_polythene_module._decode_stream

    assert decode_stream(None) == ""
    assert decode_stream("hello") == "hello"
    assert decode_stream(42) == "42"
    assert decode_stream(b"caf\xc3\xa9") == "café"
    assert "�" in decode_stream(b"\xff\xfe")


def test_format_isolation_error_returns_none_without_process_error(
    validate_polythene_module: object,
) -> None:
    """_format_isolation_error returns None when there is no ProcessExecutionError."""
    error = validate_polythene_module.ValidationError("boom")

    assert validate_polythene_module._format_isolation_error(error) is None


def test_format_isolation_error_includes_truncated_stderr(
    validate_polythene_module: object,
) -> None:
    """_format_isolation_error adds truncated stderr when patterns match."""
    stderr = "Required command not found: bwrap\n" * 50  # Ensure truncation occurs
    process_error = ProcessExecutionError(
        ("uv", "run", "polythene", "exec"),
        126,
        "",
        stderr.encode("utf-8"),
    )
    error = validate_polythene_module.ValidationError("boom")
    error.__cause__ = process_error

    message = validate_polythene_module._format_isolation_error(error)

    assert message is not None
    assert "bubblewrap" in message
    assert "Original stderr" in message
    limit = validate_polythene_module._STDERR_SNIPPET_LIMIT
    assert f"truncated to {limit}" in message
    # The message should end with an ellipsis due to truncation
    assert message.strip().endswith("…")


def test_format_isolation_error_ignores_non_matching_messages(
    validate_polythene_module: object,
) -> None:
    """_format_isolation_error returns None when patterns do not match."""
    process_error = ProcessExecutionError(
        ("uv", "run", "polythene", "exec"),
        2,
        "",
        b"some unrelated failure",
    )
    error = validate_polythene_module.ValidationError("boom")
    error.__cause__ = process_error

    assert validate_polythene_module._format_isolation_error(error) is None


def test_format_isolation_error_handles_uid_map_permission_denied(
    validate_polythene_module: object,
) -> None:
    """_format_isolation_error recognises bubblewrap permission failures."""
    stderr = "bwrap: setting up uid map: Permission denied"
    process_error = ProcessExecutionError(
        ("uv", "run", "polythene", "exec"),
        1,
        "",
        stderr.encode("utf-8"),
    )
    error = validate_polythene_module.ValidationError("boom")
    error.__cause__ = process_error

    message = validate_polythene_module._format_isolation_error(error)

    assert message is not None
    assert "sandbox dependencies" in message
    assert stderr in message


@pytest.mark.parametrize(
    "stderr_text",
    [
        pytest.param("Required command not found: bwrap", id="missing-bwrap"),
        pytest.param("Required command not found: proot", id="missing-proot"),
        pytest.param(
            "All isolation modes unavailable (bwrap/proot/chroot)",
            id="no-isolation-modes",
        ),
        pytest.param(
            "bwrap: setting up uid map: Permission denied",
            id="bwrap-permission-denied",
        ),
    ],
)
def test_polythene_rootfs_surfaces_missing_dependencies(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stderr_text: str,
) -> None:
    """Missing sandbox dependencies produce a descriptive ValidationError."""
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))

    process_error = ProcessExecutionError(
        ("uv", "run", "polythene", "exec"),
        126,
        "",
        stderr_text.encode("utf-8"),
    )

    def _run_text(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        if "pull" in argv:
            return "session-uid\n"
        error_message = "command failed"
        raise validate_polythene_module.ValidationError(
            error_message
        ) from process_error

    monkeypatch.setattr(validate_polythene_module, "run_text", _run_text)

    store = tmp_path / "store"
    polythene = tmp_path / "polythene.py"
    polythene.write_text("#!/usr/bin/env python\n")
    command = (polythene.as_posix(),)

    with (
        pytest.raises(validate_polythene_module.ValidationError) as excinfo,
        validate_polythene_module.polythene_rootfs(
            command,
            "docker.io/library/debian:bookworm",
            store,
        ),
    ):
        pass

    message = str(excinfo.value)
    assert "bubblewrap" in message
    assert "proot" in message
    assert "Original stderr" in message
    assert stderr_text in message


def test_polythene_rootfs_wraps_process_execution_error(
    validate_polythene_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unexpected ProcessExecutionError values are wrapped with stderr details."""
    monkeypatch.setattr(validate_polythene_module, "local", _FakeLocal([]))

    process_error = ProcessExecutionError(
        ("uv", "run", "polythene", "exec"),
        1,
        "",
        b"unexpected failure details",
    )

    def _run_text(command: object, *, timeout: int | None = None) -> str:
        argv = tuple(getattr(command, "argv", ()))
        if "pull" in argv:
            return "session-uid\n"
        error_message = "command failed"
        raise validate_polythene_module.ValidationError(
            error_message
        ) from process_error

    monkeypatch.setattr(validate_polythene_module, "run_text", _run_text)

    store = tmp_path / "store"
    polythene = tmp_path / "polythene.py"
    polythene.write_text("#!/usr/bin/env python\n")
    command = (polythene.as_posix(),)

    with (
        pytest.raises(validate_polythene_module.ValidationError) as excinfo,
        validate_polythene_module.polythene_rootfs(
            command,
            "docker.io/library/debian:bookworm",
            store,
        ),
    ):
        pass

    message = str(excinfo.value)
    assert "polythene exec failed" in message
    assert "stderr" in message
    assert "unexpected failure details" in message
