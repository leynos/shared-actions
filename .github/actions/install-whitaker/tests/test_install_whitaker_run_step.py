"""Exercise the `Run Whitaker installer` step on its own.

The lifecycle scenarios run every step on a POSIX host with the Linux runner
identity. This module drives the one step whose behaviour turns on things
those scenarios cannot vary: the Windows tool directory, which is derived from
`USERPROFILE` through `cygpath`, and the two output streams, each of which can
carry the installer's fallback notice.
"""

from __future__ import annotations

import dataclasses as dc
import os
import shutil
import subprocess
import typing as typ

import pytest
from _action_manifest import step_by_name

from composite_fragments import require_posix_host

if typ.TYPE_CHECKING:
    from pathlib import Path

require_posix_host()

#: Stands in for Git Bash's `cygpath`: `-u C:\Users\runner` becomes
#: `/c/Users/runner`, and `-w /c/Users/runner/x` becomes `C:\Users\runner\x`.
_CYGPATH_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" "$2" <<'PY'
import sys
mode, path = sys.argv[1], sys.argv[2]
if mode == "-u":
    print("/" + path[0].lower() + path[2:].replace("\\", "/"))
else:
    print(path[1].upper() + ":" + path[2:].replace("/", "\\"))
PY
"""

#: An installer that records the first PATH entry and its arguments, writes
#: the configured text to each stream, and exits with the configured status.
_INSTALLER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "${PATH%%:*}" > "$RECORD_DIR/path-head"
printf '%s\\n' "$*" > "$RECORD_DIR/args"
printf '%s' "$STUB_STDOUT"
printf '%s' "$STUB_STDERR" >&2
exit "$STUB_STATUS"
"""


@dc.dataclass(frozen=True)
class StepScenario:
    """Describe one run of the step: the runner and what the installer does."""

    runner_os: str = "Linux"
    extra_env: dict[str, str] = dc.field(default_factory=dict)
    stdout: str = "suite installed\n"
    stderr: str = ""
    status: int = 0


class StepRun(typ.NamedTuple):
    """The outcome of one run of the step and what its stubs recorded."""

    completed: subprocess.CompletedProcess[str]
    path_head: str
    github_path: str
    summary: str


def _write_executable(path: Path, body: str) -> None:
    """Write an executable stub."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_step(tmp_path: Path, scenario: StepScenario | None = None) -> StepRun:
    """Run the step's script under bash with stubbed tools."""
    scenario = scenario or StepScenario()
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    stubs = tmp_path / "stubs"
    _write_executable(stubs / "cygpath", _CYGPATH_STUB)
    installer = tmp_path / "cargo-home" / "bin" / "whitaker-installer"
    _write_executable(installer, _INSTALLER_STUB)
    github_path = tmp_path / "github-path"
    github_path.touch()
    summary = tmp_path / "summary.md"
    summary.touch()
    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        "RUNNER_OS": scenario.runner_os,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_PATH": str(github_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RECORD_DIR": str(tmp_path),
        "STUB_STDOUT": scenario.stdout,
        "STUB_STDERR": scenario.stderr,
        "STUB_STATUS": str(scenario.status),
        "WHITAKER_INSTALLER_PATH": str(installer),
        "WHITAKER_INSTALLER_VERSION": "0.2.9",
        "WHITAKER_CRANELIFT": "false",
        **scenario.extra_env,
    }
    script = step_by_name("Run Whitaker installer")["run"]
    assert isinstance(script, str)
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-Eeo", "pipefail", "-c", script],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
    )
    path_head = tmp_path / "path-head"
    return StepRun(
        completed,
        path_head.read_text(encoding="utf-8").strip() if path_head.exists() else "",
        github_path.read_text(encoding="utf-8").strip(),
        summary.read_text(encoding="utf-8"),
    )


class TestToolDirectory:
    """Cover where the step says the Dylint tools live, per platform."""

    def test_windows_derives_the_directory_from_the_user_profile(
        self, tmp_path: Path
    ) -> None:
        """Git Bash's HOME need not be the profile the installer uses.

        The installer sees the POSIX form on PATH, and GITHUB_PATH receives
        the native form, which is what the runner reads.
        """
        run = _run_step(
            tmp_path,
            StepScenario(
                runner_os="Windows", extra_env={"USERPROFILE": r"C:\Users\runneradmin"}
            ),
        )

        assert run.completed.returncode == 0, run.completed.stderr
        assert run.path_head == "/c/Users/runneradmin/.local/bin"
        assert run.github_path == r"C:\Users\runneradmin\.local\bin"

    def test_elsewhere_honours_xdg_bin_home(self, tmp_path: Path) -> None:
        """The installer uses XDG_BIN_HOME when set, so the step does too."""
        run = _run_step(
            tmp_path, StepScenario(extra_env={"XDG_BIN_HOME": "/opt/tools/bin"})
        )

        assert run.completed.returncode == 0, run.completed.stderr
        assert run.path_head == "/opt/tools/bin"
        assert run.github_path == "/opt/tools/bin"


class TestOutputStreams:
    """Cover the backstop, which must read both of the installer's streams."""

    @pytest.mark.parametrize(
        ("stdout", "stderr"),
        [
            pytest.param("Installing whitaker lints from source\n", "", id="stdout"),
            pytest.param(
                "suite installed\n",
                "Installed cargo-dylint from source with cargo install.\n",
                id="stderr",
            ),
        ],
    )
    def test_a_fallback_notice_on_either_stream_fails(
        self, tmp_path: Path, stdout: str, stderr: str
    ) -> None:
        """The suite's fallback is on stdout, a Dylint tool's on stderr."""
        run = _run_step(tmp_path, StepScenario(stdout=stdout, stderr=stderr))

        assert run.completed.returncode != 0
        assert "whitaker-installer.suite-source=source" in run.summary
        assert "built from source" in run.completed.stderr

    def test_the_installer_stderr_is_replayed(self, tmp_path: Path) -> None:
        """Capturing stderr for the backstop must not hide it from the log."""
        run = _run_step(
            tmp_path, StepScenario(stderr="Installing required Dylint tools...\n")
        )

        assert run.completed.returncode == 0, run.completed.stderr
        assert "Installing required Dylint tools..." in run.completed.stderr
        assert "whitaker-installer.suite-source=prebuilt" in run.summary

    def test_a_failing_installer_is_reported_with_its_status(
        self, tmp_path: Path
    ) -> None:
        """The installer's status reaches the step, with the failure notice."""
        run = _run_step(tmp_path, StepScenario(stderr="refused\n", status=34))

        assert run.completed.returncode == 34
        assert "refused" in run.completed.stderr
        assert "exit-code=34 version=0.2.9" in run.completed.stderr
        assert "whitaker-installer.failure=execution" in run.summary
