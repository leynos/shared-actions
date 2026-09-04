"""Drive the install-mdtablefix fragments against a stubbed Cargo.

A scenario describes one runner pair, one cache state, and one stubbed
``cargo`` behaviour. Running it executes the action's real Bash fragments in
declaration order, honouring each step's ``if:`` condition, so the assertions
below are about the shipped manifest rather than a paraphrase of it.

The stub refuses to install unless it is given the ``--bin-dir`` override the
action passes, mirroring cargo-binstall 1.22's rejection of mdtablefix 0.5.0's
``bin-dir = "."`` metadata. Dropping that override therefore fails the suite.
The upstream ``cargo-bins/cargo-binstall`` step cannot run outside a runner, so
the harness emulates it by making the stub's ``binstall`` subcommand available;
whether it runs at all is decided by the manifest's own condition.
"""

from __future__ import annotations

import dataclasses as dc
import os
import stat
import subprocess
from pathlib import Path

from _mdtablefix_manifest import (
    BIN_DIR_OVERRIDE,
    BINSTALL_STEP_NAME,
    manifest_steps,
)

from composite_fragments import (
    ActionContext,
    CompositeStep,
    FragmentEnvironment,
    LifecycleResult,
    StepResult,
    ambient_env,
    bash_executable,
    bash_file_path,
    bash_path,
    run_step,
)

_CARGO_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$STUB_CARGO_LOG"
if [ "${1:-}" != binstall ]; then
  echo "error: no such command: \\`${1:-}\\`" >&2
  exit 101
fi
shift
if [ "$(cat "$STUB_STATE_DIR/binstall-present")" != true ]; then
  echo "error: no such command: \\`binstall\\`" >&2
  exit 101
fi
if [ "${1:-}" = -V ]; then
  printf '%s\\n' "$STUB_BINSTALL_VERSION"
  exit 0
fi
install_path=
bin_dir_override=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-path) install_path="$2"; shift 2 ;;
    --bin-dir) bin_dir_override="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ "$STUB_BINSTALL_FAILS" = true ]; then
  echo "ERROR Fatal error: Fallback to cargo-install is disabled" >&2
  exit 94
fi
if [ "$bin_dir_override" != "$STUB_REQUIRED_BIN_DIR" ]; then
  echo "bin-dir configuration provided generates empty source path" >&2
  exit 94
fi
if [ "$STUB_INSTALL_CREATES" != true ]; then
  # cargo-binstall reported success but wrote nothing, which the action must
  # notice rather than assume.
  exit 0
fi
mkdir -p -- "$install_path"
cp -- "$STUB_STATE_DIR/installed-body" "${install_path}/mdtablefix"
chmod +x "${install_path}/mdtablefix"
"""


def _reporting_executable(output: str) -> str:
    """Return an executable body that prints ``output`` verbatim."""
    return (
        "#!/usr/bin/env bash\n"
        "cat <<'MDTABLEFIX_VERSION'\n"
        f"{output}\n"
        "MDTABLEFIX_VERSION\n"
    )


def _write_executable(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` and make it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@dc.dataclass(frozen=True)
class Scenario:
    """Describe one install-mdtablefix run."""

    tmp_path: Path
    runner_os: str = "Linux"
    runner_arch: str = "X64"
    version: str = "0.5.0"
    binstall_version: str = "1.22.0"
    bin_dir: str = "~/.local/bin"
    #: Version a pre-existing executable in ``bin-dir`` reports, if any.
    cached_version: str | None = None
    #: Exact text a pre-existing executable prints, overriding ``cached_version``.
    cached_output: str | None = None
    #: Whether ``cargo binstall -V`` succeeds before the upstream step runs.
    binstall_present: bool = True
    #: Version the stubbed binstall installs; defaults to ``version``.
    installs_version: str | None = None
    #: Exact text the installed executable prints, overriding ``installs_version``.
    installs_output: str | None = None
    #: Whether the stubbed binstall writes an executable at all. A success that
    #: leaves nothing behind is a real cargo-binstall outcome.
    install_creates_executable: bool = True
    #: Whether the stubbed binstall reports a missing prebuilt asset.
    binstall_fails: bool = False
    #: Whether the upstream cargo-binstall installer step itself fails.
    binstall_install_fails: bool = False


@dc.dataclass(frozen=True)
class ScenarioResult:
    """Aggregate what one scenario produced."""

    lifecycle: LifecycleResult
    summary: str
    github_path: str
    cargo_log: str
    #: Everything the installed executable prints, or ``None`` when none exists.
    installed_output: str | None

    @property
    def installed_version(self) -> str | None:
        """Return the version the installed executable reports, if any."""
        if self.installed_output is None:
            return None
        lines = self.installed_output.splitlines()
        first = lines[0] if lines else ""
        return first.removeprefix("mdtablefix ")

    @property
    def returncode(self) -> int:
        """Return the exit code that decided the run, or zero when it passed."""
        return self.lifecycle.returncode

    @property
    def stdout(self) -> str:
        """Return the concatenated standard output of every fragment."""
        return self.lifecycle.stdout

    @property
    def stderr(self) -> str:
        """Return the concatenated standard error of every fragment."""
        return self.lifecycle.stderr

    def executed(self) -> tuple[str, ...]:
        """Return the names of the steps that ran."""
        return self.lifecycle.executed()

    def metrics(self) -> tuple[str, ...]:
        """Return the job-summary metric lines the run emitted."""
        return tuple(
            line
            for line in self.summary.splitlines()
            if line.startswith("install-mdtablefix.")
        )


def _installed_output(executable: Path) -> str | None:
    """Return everything the installed executable prints, or ``None``.

    The executable is run rather than read, so a fixture that prints several
    lines, or a very long one, is observed exactly as the action observes it.
    """
    if not executable.is_file():
        return None
    return subprocess.run(  # noqa: S603,TID251 - a fixture the test itself wrote.
        [bash_executable(), "-c", '"$1" --version', "bash", str(executable)],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    ).stdout


@dc.dataclass(frozen=True)
class _Sandbox:
    """Hold the on-disk state one scenario runs against."""

    root: Path
    home: Path
    stub_dir: Path
    state_dir: Path
    workspace: Path
    cargo_log: Path
    summary_file: Path
    path_file: Path
    bin_dir: Path

    @property
    def binstall_marker(self) -> Path:
        """Return the file the stubbed cargo consults for its availability."""
        return self.state_dir / "binstall-present"

    @property
    def executable(self) -> Path:
        """Return where mdtablefix is expected to land."""
        return self.bin_dir / "mdtablefix"


def _build_sandbox(scenario: Scenario) -> _Sandbox:
    """Lay out the directories, stubs, and fixtures ``scenario`` describes."""
    root = scenario.tmp_path
    home = root / "home"
    if scenario.bin_dir == "~":
        bin_dir = home
    elif scenario.bin_dir.startswith("~/"):
        bin_dir = home / scenario.bin_dir[2:]
    else:
        bin_dir = Path(scenario.bin_dir)
    sandbox = _Sandbox(
        root=root,
        home=home,
        stub_dir=root / "stub-bin",
        state_dir=root / "stub-state",
        workspace=root / "workspace",
        cargo_log=root / "cargo.log",
        summary_file=root / "step-summary",
        path_file=root / "github-path",
        bin_dir=bin_dir,
    )
    for directory in (
        sandbox.home,
        sandbox.stub_dir,
        sandbox.state_dir,
        sandbox.workspace,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for artefact in (sandbox.cargo_log, sandbox.summary_file, sandbox.path_file):
        artefact.touch()
    sandbox.binstall_marker.write_text(
        "true" if scenario.binstall_present else "false",
        encoding="utf-8",
    )
    _write_executable(sandbox.stub_dir / "cargo", _CARGO_STUB)
    (sandbox.state_dir / "installed-body").write_text(
        _reporting_executable(_installed_text(scenario)),
        encoding="utf-8",
    )
    cached = _cached_text(scenario)
    if cached is not None:
        _write_executable(sandbox.executable, _reporting_executable(cached))
    return sandbox


def _installed_text(scenario: Scenario) -> str:
    """Return what the stubbed binstall's executable should print."""
    if scenario.installs_output is not None:
        return scenario.installs_output
    return f"mdtablefix {scenario.installs_version or scenario.version}"


def _cached_text(scenario: Scenario) -> str | None:
    """Return what a pre-existing executable should print, or ``None``."""
    if scenario.cached_output is not None:
        return scenario.cached_output
    if scenario.cached_version is not None:
        return f"mdtablefix {scenario.cached_version}"
    return None


def _build_environment(
    scenario: Scenario,
    sandbox: _Sandbox,
) -> FragmentEnvironment:
    """Return the environment every fragment observes."""
    return FragmentEnvironment(
        base_env={
            **ambient_env(),
            "HOME": bash_path(sandbox.home),
            # A deliberately narrow PATH: the stubbed cargo plus the system
            # utilities the fragments call. Any mdtablefix installed on the
            # test host must not be visible to the probe.
            "PATH": f"{bash_path(sandbox.stub_dir)}{os.pathsep}{os.defpath}",
            "RUNNER_OS": scenario.runner_os,
            "RUNNER_ARCH": scenario.runner_arch,
            "RUNNER_TEMP": bash_path(sandbox.root),
            "GITHUB_ENV": bash_file_path(sandbox.root / "github-env"),
            "GITHUB_PATH": bash_file_path(sandbox.path_file),
            "GITHUB_STEP_SUMMARY": bash_file_path(sandbox.summary_file),
            "STUB_BINSTALL_FAILS": "true" if scenario.binstall_fails else "false",
            "STUB_BINSTALL_VERSION": scenario.binstall_version,
            "STUB_CARGO_LOG": bash_file_path(sandbox.cargo_log),
            "STUB_INSTALL_CREATES": (
                "true" if scenario.install_creates_executable else "false"
            ),
            "STUB_REQUIRED_BIN_DIR": BIN_DIR_OVERRIDE,
            "STUB_STATE_DIR": bash_path(sandbox.state_dir),
        },
        cwd=sandbox.workspace,
        output_dir=sandbox.root / "outputs",
    )


@dc.dataclass(frozen=True)
class _Run:
    """Bundle the state one scenario's steps are executed against."""

    scenario: Scenario
    context: ActionContext
    environment: FragmentEnvironment
    sandbox: _Sandbox


def _provision_binstall(run: _Run) -> StepResult | None:
    """Emulate the upstream cargo-binstall step, which cannot run off a runner.

    On success the runner gains a usable ``cargo binstall`` and nothing is
    recorded, because the step produces no fragment output. On failure the
    step's non-zero result is recorded so the manifest's ``failure()``-guarded
    reporting step is selected, exactly as it would be on a runner.
    """
    if not run.scenario.binstall_install_fails:
        run.sandbox.binstall_marker.write_text("true", encoding="utf-8")
        return None
    run.context.succeeded = False
    return StepResult(
        name=BINSTALL_STEP_NAME,
        process=subprocess.CompletedProcess(
            args=[BINSTALL_STEP_NAME],
            returncode=1,
            stdout="",
            stderr="the upstream cargo-binstall installer failed\n",
        ),
    )


def _run_manifest_step(index: int, step: CompositeStep, run: _Run) -> StepResult | None:
    """Execute one selected manifest step and return what it produced."""
    name = step["name"]
    if name == BINSTALL_STEP_NAME:
        return _provision_binstall(run)
    process = run_step(step, run.context, run.environment, f"{index:02d}-output")
    if process.returncode != 0:
        run.context.succeeded = False
    return StepResult(name=name, process=process)


def _execute_steps(run: _Run) -> tuple[StepResult, ...]:
    """Run the selected fragments in manifest order.

    Each step's condition is evaluated only once its predecessors have run,
    because a condition reads their outputs and whether they succeeded.
    """
    results: list[StepResult] = []
    for index, step in enumerate(manifest_steps()):
        if not run.context.evaluate_condition(step):
            continue
        result = _run_manifest_step(index, step, run)
        if result is not None:
            results.append(result)
    return tuple(results)


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Execute the action's fragments for ``scenario`` and collect its record."""
    sandbox = _build_sandbox(scenario)
    context = ActionContext(
        inputs={
            "version": scenario.version,
            "binstall-version": scenario.binstall_version,
            "bin-dir": scenario.bin_dir,
        },
        runner_os=scenario.runner_os,
        runner_arch=scenario.runner_arch,
        action_path=bash_path(Path(__file__).resolve().parents[1]),
        runner_temp=bash_path(sandbox.root),
    )
    steps = _execute_steps(
        _Run(
            scenario=scenario,
            context=context,
            environment=_build_environment(scenario, sandbox),
            sandbox=sandbox,
        ),
    )

    return ScenarioResult(
        lifecycle=LifecycleResult(steps=steps),
        summary=sandbox.summary_file.read_text(encoding="utf-8"),
        github_path=sandbox.path_file.read_text(encoding="utf-8"),
        cargo_log=sandbox.cargo_log.read_text(encoding="utf-8"),
        installed_output=_installed_output(sandbox.executable),
    )
