"""Tests for polythene sandbox helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


class _FakeLocal:
    def __init__(self, calls: list[tuple[str, ...]]) -> None:
        self._calls = calls

    def __getitem__(self, value: str | tuple[str, ...]) -> _FakeCommand:
        if not isinstance(value, tuple):
            value = (value,)
        return _FakeCommand(value, self._calls)


def test_default_polythene_path_points_to_scripts(
    validate_polythene_module: object,
) -> None:
    """default_polythene_path resolves to the linux-packages script."""
    path = validate_polythene_module.default_polythene_path()

    assert path.name == "polythene.py"
    assert path.exists()


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
        tmp_path / "polythene.py",
        "sandbox-uid",
        tmp_path,
        timeout=30,
    )

    session.exec("echo", "hello")
    session.exec("echo", "world", timeout=5)

    assert calls[0][1] == 30
    assert calls[1][1] == 5


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

    with validate_polythene_module.polythene_rootfs(
        polythene,
        "docker.io/library/debian:bookworm",
        store,
        timeout=12,
    ) as session:
        assert session.script == polythene
        assert session.uid == "session-uid"
        assert session.store == store
        assert session.root.exists()
        assert session.timeout == 12

    assert cleanup_calls
    cleanup_argv = cleanup_calls[0]
    assert cleanup_argv[:4] == ("uv", "run", polythene.as_posix(), "rm")
    assert run_calls[0][1] == 12  # pull timeout
    assert run_calls[1][1] == 12  # initial exec timeout


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

    with (
        pytest.raises(validate_polythene_module.ValidationError),
        validate_polythene_module.polythene_rootfs(
            polythene,
            "docker.io/library/debian:bookworm",
            store,
        ),
    ):
        pass
