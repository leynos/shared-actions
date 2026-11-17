"""Tests for Polythene image preparation in packaging helpers."""

from __future__ import annotations

import typing as typ

import _packaging_utils as packaging_utils

if typ.TYPE_CHECKING:
    import pytest


class _FakePodmanCommand:
    """Represent a Podman command without resolving a host executable."""

    def __init__(self, argv: tuple[str, ...]) -> None:
        """Store command arguments for indexing and display."""
        self.argv = argv

    def __getitem__(self, value: str | tuple[str, ...]) -> _FakePodmanCommand:
        """Return a command with the requested arguments appended."""
        if not isinstance(value, tuple):
            value = (value,)
        return _FakePodmanCommand(self.argv + value)

    def __str__(self) -> str:
        """Render the command as a space-separated argument list."""
        return " ".join(self.argv)


class _FakeLocal:
    """Build fake commands without searching PATH for Podman."""

    def __getitem__(self, command: str) -> _FakePodmanCommand:
        """Return a fake local command rooted at ``command``."""
        return _FakePodmanCommand((command,))


def _stub_polythene_rootfs_calls(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    podman_commands: list[str],
) -> None:
    """Record image preparation calls without invoking external commands."""

    def fake_run_cmd(command: object) -> None:
        """Record the Podman command issued by the helper."""
        events.append("podman-pull")
        podman_commands.append(str(command))

    def fake_polythene_cmd(
        _polythene: packaging_utils.Command,
        *_args: str,
    ) -> str:
        """Record the Polythene image export and return a rootfs identifier."""
        events.append("polythene-pull")
        return "test-uid\n"

    def fake_polythene_exec(
        _polythene: packaging_utils.Command,
        _uid: str,
        _store: str,
        *_command: str,
    ) -> str:
        """Record the Polythene isolation probe."""
        events.append("polythene-exec")
        return ""

    monkeypatch.setattr(packaging_utils, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(packaging_utils, "local", _FakeLocal())
    monkeypatch.setattr(packaging_utils, "polythene_cmd", fake_polythene_cmd)
    monkeypatch.setattr(packaging_utils, "polythene_exec", fake_polythene_exec)


def test_polythene_rootfs_refreshes_registry_image_before_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opted-in registry images are refreshed before Polythene exports them."""
    events: list[str] = []
    podman_commands: list[str] = []
    _stub_polythene_rootfs_calls(monkeypatch, events, podman_commands)

    image = "docker.io/library/rockylinux:9"
    with packaging_utils.polythene_rootfs(
        packaging_utils.DEFAULT_POLYTHENE_COMMAND,
        image,
        refresh_registry_image=True,
    ):
        pass

    assert events == ["podman-pull", "polythene-pull", "polythene-exec"]
    assert len(podman_commands) == 1
    assert "pull" in podman_commands[0].split()
    assert image in podman_commands[0]


def test_polythene_rootfs_keeps_local_only_image_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path does not try to pull a local-only image from a registry."""
    events: list[str] = []
    podman_commands: list[str] = []
    _stub_polythene_rootfs_calls(monkeypatch, events, podman_commands)

    with packaging_utils.polythene_rootfs(
        packaging_utils.DEFAULT_POLYTHENE_COMMAND,
        "localhost/local-only:dev",
    ):
        pass

    assert events == ["polythene-pull", "polythene-exec"]
    assert podman_commands == []
