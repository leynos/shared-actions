"""Tests for the `sccache-report` composite action's report step.

A consumer used to repeat `sccache --show-stats` after its build. With
`setup-rust` failing open (shared-actions #546), a server that never started has
no statistics, and asking for them starts it again and fails the job the
fallback had just saved. This action owns that guard once; the tests run its
shipped script against a stub sccache.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
STEP = "Report sccache statistics"

GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _bash() -> str:
    """Return the Bash the action's `shell: bash` uses on this host.

    On GitHub's Windows images that is Git Bash. A plain `bash` lookup there
    can find WSL's launcher in System32 instead, which is a different shell
    over a different filesystem, so the candidates are tried explicitly and
    any System32 hit is ignored. The behaviour tests skip only when no Git
    Bash exists, naming every path tried.
    """
    if sys.platform != "win32":
        return shutil.which("bash") or "bash"
    tried = list(GIT_BASH_CANDIDATES)
    for candidate in tried:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    tried.append(f"shutil.which('bash') -> {found}")
    pytest.skip(f"no Git Bash found; tried {', '.join(tried)}")
    return ""  # pragma: no cover - pytest.skip raises


#: A callable that runs the shipped script with overrides and returns its result.
Runner = typ.Callable[..., "Run"]


def _step() -> dict[str, typ.Any]:
    steps = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]
    return next(step for step in steps if step["name"] == STEP)


class Run(typ.NamedTuple):
    """One run of the report script and the files it wrote."""

    completed: subprocess.CompletedProcess[str]
    home: Path

    def read(self, name: str) -> str:
        """Return a file the script wrote, or an empty string."""
        path = self.home / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def outputs(self) -> dict[str, str]:
        """Return the step outputs as a mapping."""
        pairs = (
            line.split("=", 1)
            for line in self.read("github_output").splitlines()
            if "=" in line
        )
        return dict(pairs)


@pytest.fixture
def run_report(tmp_path: Path) -> typ.Callable[..., Run]:
    """Return a runner for the shipped script with a stub sccache on PATH."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "sccache"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$(dirname "$0")/calls.log"\n'
        'if [[ "$*" == *json* ]]; then\n'
        "  echo '{\"stats\":{}}'\n"
        "else\n"
        '  echo "Compile requests 7"\n'
        "fi\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    def run(*, with_stub: bool = True, **env: str) -> Run:
        bash = _bash()
        # Without the stub, only the shell's own tools are on PATH, so a real
        # sccache on the host cannot answer.
        system_tools = (
            str(Path(bash).parent.parent / "usr" / "bin")
            if sys.platform == "win32"
            else "/usr/bin:/bin"
        )
        path = (
            f"{stub_dir}{os.pathsep}{os.environ['PATH']}" if with_stub else system_tools
        )
        environment = {
            "PATH": path,
            "SR_STATUS": "",
            "SR_BACKEND": "",
            "SR_STATS_FILE": str(tmp_path / "sccache-stats.json"),
            "SR_TEXT_FILE": str(tmp_path / "sccache-stats.txt"),
            "SR_SUMMARY": "true",
            "GITHUB_OUTPUT": str(tmp_path / "github_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            **env,
        }
        completed = subprocess.run(  # noqa: S603,TID251 - the script under test.
            [bash, "-c", _step()["run"]],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=30,
        )
        return Run(completed, tmp_path)

    return run


def _calls(run: Run) -> str:
    path = run.home / "bin" / "calls.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestFallback:
    """A fallen-back server has no statistics, so nothing may ask for them."""

    def test_a_fallback_never_calls_sccache(self, run_report: Runner) -> None:
        """Calling sccache would start the dead server again."""
        result = run_report(SR_STATUS="fallback")

        assert result.completed.returncode == 0, result.completed.stderr
        assert _calls(result) == ""

    def test_a_fallback_reports_false_and_writes_nothing(
        self, run_report: Runner
    ) -> None:
        """The consumer's health check reads `reported` and skips."""
        result = run_report(SR_STATUS="fallback")

        assert result.outputs() == {"reported": "false", "stats-file": ""}
        assert result.read("sccache-stats.json") == ""
        assert result.read("summary") == ""
        assert "fell back" in result.completed.stdout

    def test_a_fallback_is_still_visible(self, run_report: Runner) -> None:
        """Standing down must say so, or a missing report looks like a bug."""
        result = run_report(SR_STATUS="fallback")

        assert "::notice title=sccache-report::" in result.completed.stdout
        assert "metric sccache-report.outcome=fallback" in result.completed.stdout


class TestReporting:
    """Any other status reports, in the log, the files and the summary."""

    @pytest.mark.parametrize("status", ["", "started"])
    def test_it_reports_statistics(self, run_report: Runner, status: str) -> None:
        """An empty status (no server started by setup-rust) still reports."""
        result = run_report(SR_STATUS=status, SR_BACKEND="ubicloud")

        assert result.completed.returncode == 0, result.completed.stderr
        assert "Compile requests 7" in result.completed.stdout
        assert "Compile requests 7" in result.read("sccache-stats.txt")
        assert result.read("sccache-stats.json").strip() == '{"stats":{}}'
        assert result.outputs()["reported"] == "true"
        assert result.outputs()["stats-file"].endswith("sccache-stats.json")

    def test_the_summary_names_the_backend(self, run_report: Runner) -> None:
        """`Cache location` cannot tell Ubicloud's proxy from GitHub's service."""
        result = run_report(SR_STATUS="started", SR_BACKEND="ubicloud")

        summary = result.read("summary")
        assert "- backend: `ubicloud`" in summary
        assert "Compile requests 7" in summary

    def test_the_summary_can_be_switched_off(self, run_report: Runner) -> None:
        """A caller with its own summary format opts out."""
        result = run_report(SR_STATUS="started", SR_SUMMARY="false")

        assert result.read("summary") == ""
        assert result.outputs()["reported"] == "true"

    def test_it_asks_sccache_for_text_and_json(self, run_report: Runner) -> None:
        """Both formats are written, since consumers' health checks read JSON."""
        result = run_report(SR_STATUS="started")

        calls = _calls(result).splitlines()
        assert "--show-stats" in calls
        assert "--show-stats --stats-format json" in calls


class TestNoSccache:
    """A job that failed before sccache existed must not gain a second failure."""

    def test_a_missing_binary_stands_down(self, run_report: Runner) -> None:
        """Reporting nothing is correct; failing would bury the real error."""
        result = run_report(with_stub=False, SR_STATUS="started")

        assert result.completed.returncode == 0, result.completed.stderr
        assert result.outputs()["reported"] == "false"
        assert "metric sccache-report.outcome=not-installed" in result.completed.stdout


class TestManifest:
    """The wiring consumers depend on."""

    def test_the_outputs_read_the_report_step(self) -> None:
        """`reported` and `stats-file` must come from the step that decides."""
        manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))

        assert manifest["outputs"]["reported"]["value"] == (
            "${{ steps.report.outputs.reported }}"
        )
        assert manifest["outputs"]["stats-file"]["value"] == (
            "${{ steps.report.outputs.stats-file }}"
        )
        assert _step()["id"] == "report"

    def test_the_step_is_bash(self) -> None:
        """The script uses Bash features, so the shell is pinned to it."""
        assert _step()["shell"] == "bash"

    def test_the_inputs_have_the_documented_defaults(self) -> None:
        """A caller that sets only `status` gets the files setup-rust users expect."""
        inputs = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["inputs"]

        assert inputs["stats-file"]["default"] == "sccache-stats.json"
        assert inputs["text-file"]["default"] == "sccache-stats.txt"
        assert inputs["summary"]["default"] == "true"
