"""Tests for act runtime probing in the workflow harness."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

from . import conftest


def test_command_available_accepts_absolute_executable() -> None:
    """Absolute executable paths are reported as available."""
    assert conftest._command_available(sys.executable)


def test_command_available_rejects_non_executable_file(tmp_path: Path) -> None:
    """Absolute non-executable files are not reported as available."""
    path = tmp_path / "not-executable"
    path.write_text("#!/bin/sh\n", encoding="utf-8")

    assert not conftest._command_available(str(path))


def test_command_succeeds_reports_successful_command() -> None:
    """Successful commands return True."""
    assert conftest._command_succeeds(sys.executable, "-c", "pass")


def test_command_succeeds_reports_failed_command() -> None:
    """Non-zero commands return False."""
    assert not conftest._command_succeeds(
        sys.executable, "-c", "import sys; sys.exit(3)"
    )


def test_command_succeeds_reports_missing_command() -> None:
    """Missing commands return False."""
    assert not conftest._command_succeeds("definitely-not-a-real-command-for-tests")


def test_probe_reports_unhealthy_podman_docker_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale Podman store should skip act tests before act is invoked."""
    socket_path = tmp_path / "podman.sock"
    socket_path.touch()

    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)
    monkeypatch.setattr(conftest.shutil, "which", lambda command: command)
    monkeypatch.setattr(conftest, "_command_succeeds", lambda *_args: False)
    monkeypatch.setattr(
        conftest,
        "_docker_host_usable",
        lambda _host: (False, "Docker API cannot list containers: container not known"),
    )

    status = conftest._probe_act_runtime({"XDG_RUNTIME_DIR": str(tmp_path)})

    assert not status.available
    assert "podman Docker API is not usable for act" in status.reason
    assert "container not known" in status.reason


def test_probe_exports_podman_docker_host_when_socket_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A healthy Podman socket should be forwarded to act automatically."""
    podman_dir = tmp_path / "podman"
    podman_dir.mkdir()
    socket_path = podman_dir / "podman.sock"
    socket_path.touch()

    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)
    monkeypatch.setattr(
        conftest.shutil,
        "which",
        lambda command: command if command == "podman" else None,
    )
    monkeypatch.setattr(conftest, "_command_succeeds", lambda *_args: False)
    monkeypatch.setattr(conftest, "_docker_host_usable", lambda _host: (True, ""))

    status = conftest._probe_act_runtime({"XDG_RUNTIME_DIR": str(tmp_path)})

    assert status.available
    assert status.env == {"DOCKER_HOST": f"unix://{socket_path}"}


def test_probe_honours_configured_act_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Makefile-provided ACT path should be used for discovery."""
    seen: list[str] = []

    def command_available(command: str) -> bool:
        seen.append(command)
        return False

    monkeypatch.setattr(conftest, "_command_available", command_available)

    status = conftest._probe_act_runtime({"ACT": "/custom/bin/act"})

    assert not status.available
    assert seen == ["/custom/bin/act"]
    assert status.reason == "act executable not found: /custom/bin/act"


def test_docker_host_unix_socket_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCKER_HOST=unix://... with a non-existent socket path reports a clear error."""
    docker_host = "unix:///this/path/does/not/exist.sock"
    monkeypatch.setenv("DOCKER_HOST", docker_host)

    usable, reason = conftest._docker_host_usable(os.environ["DOCKER_HOST"])

    assert usable is False
    assert reason.startswith("Docker API socket does not exist:")


def test_docker_host_unix_socket_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """DOCKER_HOST=unix://... with an unreachable socket surfaces the OSError."""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()

    docker_host = f"unix://{socket_path}"
    monkeypatch.setenv("DOCKER_HOST", docker_host)

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        msg = "test unreachable docker socket"
        raise OSError(msg)

    monkeypatch.setattr(conftest, "_read_unix_http", _raise_oserror)

    usable, reason = conftest._docker_host_usable(os.environ["DOCKER_HOST"])

    assert usable is False
    assert reason.startswith("Docker API socket is not reachable:")


def test_probe_reports_missing_docker_host_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe surfaces a missing DOCKER_HOST Unix socket as unavailable."""
    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)

    status = conftest._probe_act_runtime(
        {"DOCKER_HOST": "unix:///nonexistent/path.sock"}
    )

    assert not status.available
    assert "Docker API socket does not exist:" in status.reason


def test_probe_reports_unreachable_docker_host_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Probe surfaces an unreachable DOCKER_HOST Unix socket as unavailable."""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()

    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        msg = "test unreachable docker socket"
        raise OSError(msg)

    monkeypatch.setattr(conftest, "_read_unix_http", _raise_oserror)

    status = conftest._probe_act_runtime({"DOCKER_HOST": f"unix://{socket_path}"})

    assert not status.available
    assert "Docker API socket is not reachable:" in status.reason


def test_skip_marker_uses_act_runtime_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Marked workflow tests are skipped when the act runtime probe is unavailable."""

    class MarkedItem:
        def __init__(self) -> None:
            self.keywords = {"skip_unless_act": object()}

    status = conftest.ActRuntimeStatus(
        available=False,
        reason="act unavailable in test",
        env={},
    )
    monkeypatch.setattr(conftest, "_probe_act_runtime", lambda: status)
    conftest._get_act_runtime_status.cache_clear()

    with pytest.raises(pytest.skip.Exception, match="act unavailable in test"):
        conftest.pytest_runtest_setup(typ.cast("pytest.Item", MarkedItem()))
