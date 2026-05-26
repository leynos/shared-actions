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
