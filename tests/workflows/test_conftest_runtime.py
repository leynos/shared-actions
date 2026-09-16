"""Tests for act runtime probing in the workflow harness."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

from . import conftest


class TestExecutableDetection:
    """How the harness decides a path names a runnable command.

    Grouped because they answer one question between them, and
    because the POSIX and Windows rules are different answers to it:
    a permission bit on one, a PATHEXT suffix on the other.
    """

    def test_command_available_accepts_absolute_executable(self) -> None:
        """Absolute executable paths are reported as available."""
        assert conftest._command_available(sys.executable), (
            f"the running interpreter {sys.executable!r} is an absolute path to "
            "an executable file and must be reported as available"
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "Windows has no execute bit: os.access(..., X_OK) is true for any "
            "existing file, so the property this asserts does not exist there. "
            "The reading gates act, which these tests skip on Windows anyway."
        ),
    )
    def test_command_available_rejects_non_executable_file(
        self, tmp_path: Path
    ) -> None:
        """Absolute non-executable files are not reported as available.

        POSIX only. See the skip reason: the execute bit this depends on has
        no Windows equivalent, and the reading's only caller gates act.
        """
        path = tmp_path / "not-executable"
        path.write_text("#!/bin/sh\n", encoding="utf-8")

        assert not conftest._command_available(str(path)), (
            f"{path} has no execute permission, so it must not be reported as "
            "a runnable command"
        )

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param("tool.exe", True, id="pathext-suffix"),
            pytest.param("tool.EXE", True, id="pathext-suffix-upper-case"),
            pytest.param("tool", False, id="no-suffix"),
            pytest.param("tool.sh", False, id="suffix-outside-pathext"),
        ],
    )
    def test_is_executable_file_uses_pathext_on_windows(
        self,
        tmp_path: Path,
        name: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Windows executability follows PATHEXT, not a permission bit.

        The file is given no execute permission on any platform, so a
        permission-bit probe would reject every case. Only the suffix rule
        accepts the two PATHEXT names, which is what Windows itself does.
        """
        path = tmp_path / name
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o600)

        assert conftest._is_executable_file(path, on_windows=True) is expected, (
            f"on Windows, {name!r} carries the suffix "
            f"{path.suffix.lower()!r}, so it should "
            f"{'be' if expected else 'not be'} treated as executable; PATHEXT "
            f"here is {sorted(conftest._windows_executable_suffixes())}"
        )

    def test_is_executable_file_rejects_a_directory_on_windows(
        self, tmp_path: Path
    ) -> None:
        """A directory whose name carries a PATHEXT suffix is not a command."""
        directory = tmp_path / "tool.exe"
        directory.mkdir()

        assert not conftest._is_executable_file(directory, on_windows=True), (
            f"{directory} is a directory, not a file, so its .exe suffix must "
            "not make it a command"
        )

    def test_windows_executable_suffixes_reads_the_environment(self) -> None:
        """PATHEXT is split on the path separator and lower-cased."""
        suffixes = conftest._windows_executable_suffixes(
            {"PATHEXT": ".EXE;.Cmd;; "},
        )

        assert suffixes == frozenset({".exe", ".cmd"}), (
            f"PATHEXT '.EXE;.Cmd;; ' should lower-case, drop the empty entry "
            f"and drop the blank one, giving {{'.exe', '.cmd'}}, got {suffixes}"
        )

    def test_windows_executable_suffixes_falls_back_when_pathext_is_empty(
        self,
    ) -> None:
        """An absent or empty PATHEXT falls back to the documented defaults."""
        absent = conftest._windows_executable_suffixes({})
        empty = conftest._windows_executable_suffixes({"PATHEXT": ""})

        assert absent == empty, (
            "an absent PATHEXT and an empty one are the same absence of a "
            f"caller preference, but gave {absent} and {empty}"
        )
        assert ".exe" in absent, (
            f"the documented fallback must include '.exe', got {absent}"
        )


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        pytest.param(sys.executable, ("-c", "pass"), True, id="successful"),
        pytest.param(
            sys.executable,
            ("-c", "import sys; sys.exit(3)"),
            False,
            id="failed",
        ),
        pytest.param(
            "definitely-not-a-real-command-for-tests",
            (),
            False,
            id="missing",
        ),
    ],
)
def test_command_succeeds_reports_command_status(
    command: str,
    args: tuple[str, ...],
    expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
) -> None:
    """Command success probes return the expected boolean."""
    assert conftest._command_succeeds(command, *args) is expected, (
        f"_command_succeeds({command!r}, *{args}) != {expected}"
    )


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


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        pytest.param("missing", "Docker API socket does not exist:", id="missing"),
        pytest.param(
            "unreachable",
            "Docker API socket is not reachable:",
            id="unreachable",
        ),
    ],
)
def test_docker_host_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    expected_reason: str,
) -> None:
    """Docker host failures are reported consistently by helpers and probes."""
    if failure == "missing":
        docker_host = "unix:///nonexistent/path.sock"
    else:
        socket_path = tmp_path / "docker.sock"
        socket_path.touch()
        docker_host = f"unix://{socket_path}"

        def _raise_oserror(*_args: object, **_kwargs: object) -> None:
            msg = "test unreachable docker socket"
            raise OSError(msg)

        monkeypatch.setattr(conftest, "_read_unix_http", _raise_oserror)

    monkeypatch.setenv("DOCKER_HOST", docker_host)
    monkeypatch.setattr(conftest, "_command_available", lambda _command: True)

    usable, reason = conftest._docker_host_usable(os.environ["DOCKER_HOST"])
    status = conftest._probe_act_runtime({"DOCKER_HOST": docker_host})

    assert usable is False
    assert expected_reason in reason

    assert not status.available
    assert expected_reason in status.reason


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
