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
import re
import stat
import typing as typ
from pathlib import Path

from _mdtablefix_manifest import (
    BIN_DIR_OVERRIDE,
    BINSTALL_STEP_NAME,
    manifest_steps,
)

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    LifecycleResult,
    StepResult,
    ambient_env,
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
mkdir -p -- "$install_path"
cat > "${install_path}/mdtablefix" <<STUB_EXECUTABLE
#!/usr/bin/env bash
printf 'mdtablefix %s\\n' "$STUB_INSTALL_VERSION"
STUB_EXECUTABLE
chmod +x "${install_path}/mdtablefix"
"""

_MDTABLEFIX_STUB = """#!/usr/bin/env bash
printf 'mdtablefix %s\\n' "{version}"
"""

#: Recovers the version an installed stub executable reports.
_STUB_VERSION = re.compile(r"printf 'mdtablefix %s..' \"(?P<version>[^\"]+)\"")


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
    #: Whether ``cargo binstall -V`` succeeds before the upstream step runs.
    binstall_present: bool = True
    #: Version the stubbed binstall installs; defaults to ``version``.
    installs_version: str | None = None
    #: Whether the stubbed binstall reports a missing prebuilt asset.
    binstall_fails: bool = False


@dc.dataclass(frozen=True)
class ScenarioResult:
    """Aggregate what one scenario produced."""

    lifecycle: LifecycleResult
    summary: str
    github_path: str
    cargo_log: str
    installed_version: str | None

    @property
    def returncode(self) -> int:
        """Return the exit code of the last fragment that ran."""
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


def _installed_version(executable: Path) -> str | None:
    """Return the version an installed stub reports, or ``None``."""
    if not executable.is_file():
        return None
    match = _STUB_VERSION.search(executable.read_text(encoding="utf-8"))
    return match["version"] if match is not None else None


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Execute the action's fragments for ``scenario`` and collect its record."""
    root = scenario.tmp_path
    home = root / "home"
    stub_dir = root / "stub-bin"
    state_dir = root / "stub-state"
    workspace = root / "workspace"
    for directory in (home, stub_dir, state_dir, workspace):
        directory.mkdir(parents=True, exist_ok=True)

    cargo_log = root / "cargo.log"
    cargo_log.touch()
    summary_file = root / "step-summary"
    summary_file.touch()
    path_file = root / "github-path"
    path_file.touch()
    (state_dir / "binstall-present").write_text(
        "true" if scenario.binstall_present else "false",
        encoding="utf-8",
    )
    _write_executable(stub_dir / "cargo", _CARGO_STUB)

    resolved_bin_dir = (
        home / scenario.bin_dir[2:]
        if scenario.bin_dir.startswith("~/")
        else Path(scenario.bin_dir)
    )
    if scenario.cached_version is not None:
        _write_executable(
            resolved_bin_dir / "mdtablefix",
            _MDTABLEFIX_STUB.format(version=scenario.cached_version),
        )

    base_env = {
        **ambient_env(),
        "HOME": bash_path(home),
        # A deliberately narrow PATH: the stubbed cargo plus the system
        # utilities the fragments call. Any mdtablefix installed on the test
        # host must not be visible to the probe.
        "PATH": f"{bash_path(stub_dir)}{os.pathsep}{os.defpath}",
        "RUNNER_OS": scenario.runner_os,
        "RUNNER_ARCH": scenario.runner_arch,
        "RUNNER_TEMP": bash_path(root),
        "GITHUB_ENV": bash_file_path(root / "github-env"),
        "GITHUB_PATH": bash_file_path(path_file),
        "GITHUB_STEP_SUMMARY": bash_file_path(summary_file),
        "STUB_BINSTALL_FAILS": "true" if scenario.binstall_fails else "false",
        "STUB_BINSTALL_VERSION": scenario.binstall_version,
        "STUB_CARGO_LOG": bash_file_path(cargo_log),
        "STUB_INSTALL_VERSION": scenario.installs_version or scenario.version,
        "STUB_REQUIRED_BIN_DIR": BIN_DIR_OVERRIDE,
        "STUB_STATE_DIR": bash_path(state_dir),
    }

    context = ActionContext(
        inputs={
            "version": scenario.version,
            "binstall-version": scenario.binstall_version,
            "bin-dir": scenario.bin_dir,
        },
        runner_os=scenario.runner_os,
        runner_arch=scenario.runner_arch,
        action_path=bash_path(Path(__file__).resolve().parents[1]),
        runner_temp=bash_path(root),
    )
    environment = FragmentEnvironment(
        base_env=base_env,
        cwd=workspace,
        output_dir=root / "outputs",
    )

    results: list[StepResult] = []
    for index, step in enumerate(manifest_steps()):
        if not context.evaluate_condition(step):
            continue
        name = typ.cast("str", step["name"])
        if name == BINSTALL_STEP_NAME:
            # Emulate the upstream composite action: after it runs, the
            # runner has a usable `cargo binstall`.
            (state_dir / "binstall-present").write_text("true", encoding="utf-8")
            continue
        process = run_step(step, context, environment, f"{index:02d}-output")
        results.append(StepResult(name=name, process=process))
        if process.returncode != 0:
            break

    return ScenarioResult(
        lifecycle=LifecycleResult(steps=tuple(results)),
        summary=summary_file.read_text(encoding="utf-8"),
        github_path=path_file.read_text(encoding="utf-8"),
        cargo_log=cargo_log.read_text(encoding="utf-8"),
        installed_version=_installed_version(resolved_bin_dir / "mdtablefix"),
    )
