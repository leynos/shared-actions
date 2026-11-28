"""Behaviour of the snapshot install step, run against a stub `uv`."""

from __future__ import annotations

import os
import stat
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_kani_snapshot.sh"
SOURCE = "git+https://github.com/leynos/rust-prover-tools@" + "f" * 40
TOKEN = "ghs_not_a_real_token_value"  # noqa: S105  # a marker, not a credential

# Records each call's arguments, and fails only the `snapshot --help` probe when
# asked, so the script's decisions can be read without a network or a tool.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
printf '%s\\n' "${INPUT_VERSION-unset}" >> "$STUB_LOG.env"
case "$*" in
  *"kani snapshot --help"*)
    [ "${STUB_PROBE_EXIT:-0}" = 0 ] ||
      echo "${STUB_PROBE_MESSAGE:-Unknown command snapshot}"
    exit "${STUB_PROBE_EXIT:-0}" ;;
esac
exit "${STUB_EXIT:-0}"
"""


class Run(typ.NamedTuple):
    """A finished script run and the calls the stub saw."""

    code: int
    output: str
    calls: list[str]


@pytest.fixture
def run(tmp_path: Path) -> typ.Callable[..., Run]:
    """Return a runner that executes the script with a stub `uv` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(STUB, encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.log"

    def _run(**extra: str) -> Run:
        """Run the script, with *extra* environment entries replacing defaults."""
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "SOURCE": SOURCE,
            "ROOT": str(tmp_path),
            "PIN": str(tmp_path / "tools" / "kani" / "snapshot.toml"),
            "GH_TOKEN": TOKEN,
            **extra,
        }
        command = local["bash"][str(SCRIPT)].with_env(**env)
        code, stdout, stderr = command.run(retcode=None)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return Run(code, stdout + stderr, calls)

    return _run


def test_install_then_check_run_with_the_pin_and_repo_root(
    run: typ.Callable[..., Run], tmp_path: Path
) -> None:
    """The tool is called with the pinned source, the repo root and the pin."""
    result = run()

    pin = tmp_path / "tools" / "kani" / "snapshot.toml"
    prefix = f"tool run --from {SOURCE} prover-tools kani snapshot"
    assert result.code == 0, result.output
    assert result.calls == [
        f"{prefix} --help",
        f"{prefix} install --repo-root {tmp_path} --pin {pin}",
        f"{prefix} check --repo-root {tmp_path} --pin {pin}",
    ], "probe, install, then check, with no cache override"


def test_a_revision_without_snapshots_fails_naming_the_fix(
    run: typ.Callable[..., Run],
) -> None:
    """A missing subcommand stops before installing and names prover-tools-sha."""
    result = run(STUB_PROBE_EXIT="2")

    assert result.code == 1, "the step fails"
    assert "prover-tools-sha" in result.output, "the input to change is named"
    assert "f3ac307" in result.output, "the earliest good revision is named"
    assert len(result.calls) == 1, "only the probe ran"


def test_another_probe_failure_is_shown_not_renamed(
    run: typ.Callable[..., Run],
) -> None:
    """A fault that is not a missing command keeps the tool's own message."""
    result = run(STUB_PROBE_EXIT="1", STUB_PROBE_MESSAGE="network unreachable")

    assert result.code == 1, "the step fails"
    assert "network unreachable" in result.output, "the cause is shown"
    assert "has no 'kani snapshot' command" not in result.output, "not misreported"


def test_a_failed_install_stops_before_the_check(run: typ.Callable[..., Run]) -> None:
    """The install's failure is the step's failure."""
    result = run(STUB_EXIT="1")

    assert result.code != 0, "the failure propagates"
    assert not any(" check " in call for call in result.calls), "no check after it"


def test_the_token_is_never_echoed(run: typ.Callable[..., Run]) -> None:
    """Neither success nor either failure prints the token or its arguments."""
    for extra in ({}, {"STUB_PROBE_EXIT": "2"}, {"STUB_EXIT": "1"}):
        result = run(**extra)
        assert TOKEN not in result.output, extra
        assert not any(TOKEN in call for call in result.calls), "never an argument"


def result_log(tmp_path: Path) -> Path:
    """Return the stub's call log path."""
    return tmp_path / "calls.log"


def test_the_caller_environment_cannot_steer_the_tool(
    run: typ.Callable[..., Run], tmp_path: Path
) -> None:
    """Ambient INPUT_* names are scrubbed before the tool runs."""
    result = run(INPUT_VERSION="9.9.9", INPUT_KANI_COMMAND="evil")

    assert result.code == 0, result.output
    seen = Path(f"{result_log(tmp_path)}.env").read_text(encoding="utf-8")
    assert set(seen.split()) == {"unset"}, "the tool never saw INPUT_VERSION"
