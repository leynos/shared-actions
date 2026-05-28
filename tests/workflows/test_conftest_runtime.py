"""Tests for act runtime probing in the workflow harness."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

from . import conftest


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
    import os

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
    import os

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
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/path.sock")

    status = conftest._probe_act_runtime()

    assert not status.available
    assert "Docker API socket does not exist:" in status.reason


def test_probe_reports_unreachable_docker_host_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Probe surfaces an unreachable DOCKER_HOST Unix socket as unavailable."""
    import os

    socket_path = tmp_path / "docker.sock"
    socket_path.touch()

    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)
    monkeypatch.setenv("DOCKER_HOST", f"unix://{socket_path}")

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        msg = "test unreachable docker socket"
        raise OSError(msg)

    monkeypatch.setattr(conftest, "_read_unix_http", _raise_oserror)

    status = conftest._probe_act_runtime(os.environ.copy())

    assert not status.available
    assert "Docker API socket is not reachable:" in status.reason
