"""Contract and behaviour tests for the df12-skylos and df12-duplication actions.

Both actions are thin: they validate their inputs, provision the pinned
df12-python-lints package through ``uv tool run`` and report the exit status of
the installed command. The commands' own behaviour is tested once, in
df12-python-lints; these tests pin only what the actions add. The script of each
action runs against a stubbed ``uv`` that records its arguments.
"""

from __future__ import annotations

import re
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX shell stubs; the actions run on Linux/macOS"
)

ACTIONS_ROOT = Path(__file__).resolve().parents[1]
GIT_URL = "git+https://github.com/leynos/df12-python-lints.git"
ACTIONS = {
    "df12-skylos": ("skylos", ("check",)),
    "df12-duplication": ("duplication", ("install", "check")),
}


def _manifest(name: str) -> dict[str, typ.Any]:
    """Load an action manifest."""
    return yaml.safe_load((ACTIONS_ROOT / name / "action.yml").read_text("utf-8"))


def _check_step(name: str) -> dict[str, typ.Any]:
    """Return the step that runs the installed command."""
    (step,) = [s for s in _manifest(name)["runs"]["steps"] if s.get("id") == "check"]
    return step


def _spec(name: str) -> str:
    """Return the literal package spec in the action's step env, as YAML folds it."""
    return _check_step(name)["env"]["DF12_SPEC"]


def _run(
    tmp_path: Path, name: str, *, stub_body: str = "", **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Run an action's script with a recording ``uv`` stub on ``PATH``."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    log = tmp_path / "uv.log"
    uv = stubs / "uv"
    uv.write_text(
        f'#!/bin/sh\necho "$*" >> "{log}"\n{stub_body}\nexit 0\n', encoding="utf-8"
    )
    uv.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "DF12_SPEC": _spec(name),
        "DF12_REPOSITORY": str(repo),
        "DF12_PYTHON": "3.14",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "HOME": str(tmp_path),
    }
    env.update(overrides)
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        ["/bin/bash", "-c", _check_step(name)["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(tmp_path: Path) -> list[str]:
    """Return the recorded ``uv`` invocations."""
    log = tmp_path / "uv.log"
    return log.read_text("utf-8").splitlines() if log.exists() else []


@pytest.mark.parametrize("name", ACTIONS)
def test_manifest_declares_the_contract(name: str) -> None:
    """Inputs and the output are declared; nothing is cached."""
    manifest = _manifest(name)
    assert set(manifest["inputs"]) == {"repository", "python-version"}
    assert set(manifest["outputs"]) == {"exit-status"}
    uses = [s["uses"] for s in manifest["runs"]["steps"] if "uses" in s]
    assert all(re.search(r"@[0-9a-f]{40}$", u) for u in uses), uses
    assert not any(u.startswith("actions/cache") for u in uses)


@pytest.mark.parametrize("name", ACTIONS)
def test_setup_uv_is_skipped_when_the_caller_already_has_uv(name: str) -> None:
    """A caller's own uv is kept: setup-uv runs only when uv is absent from PATH."""
    steps = _manifest(name)["runs"]["steps"]
    names = [step.get("name") for step in steps]
    detect = steps[names.index("Detect uv")]
    setup = steps[names.index("Set up uv")]
    assert names.index("Detect uv") < names.index("Set up uv")
    assert detect["id"] == "uv"
    assert detect["env"]["PROBE_TOOL"] == "uv"
    assert 'command -v "${PROBE_TOOL}"' in detect["run"]
    assert setup["if"] == "steps.uv.outputs.present != 'true'"


@pytest.mark.parametrize(("name", "plan"), ACTIONS.items())
def test_the_spec_is_one_literal_full_commit_pin(
    name: str, plan: tuple[str, tuple[str, ...]]
) -> None:
    """Concordat UV-006 reads a literal: extras form, Git URL, full commit."""
    spec = _spec(name)
    assert "${" not in spec
    assert re.fullmatch(
        rf"df12-python-lints\[{plan[0]}\] @ {re.escape(GIT_URL)}@[0-9a-f]{{40}}", spec
    ), spec


def test_both_actions_pin_the_same_package_commit() -> None:
    """One pin for the estate: the two literals cannot drift apart."""
    pins = {_spec(n).rsplit("@", 1)[1] for n in ACTIONS}
    assert len(pins) == 1


@pytest.mark.parametrize("name", ACTIONS)
def test_inputs_reach_the_script_only_through_env(name: str) -> None:
    """No expression is interpolated into the script text."""
    step = _check_step(name)
    assert "${{" not in step["run"]
    assert set(step["env"]) == {"DF12_SPEC", "DF12_REPOSITORY", "DF12_PYTHON"}


@posix_only
@pytest.mark.parametrize(("name", "plan"), ACTIONS.items())
def test_clean_run_provisions_the_pinned_spec_and_reports_zero(
    tmp_path: Path, name: str, plan: tuple[str, tuple[str, ...]]
) -> None:
    """The command is invoked from the SHA-pinned extras spec, once per verb."""
    extra, verbs = plan
    result = _run(tmp_path, name)
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert len(calls) == len(verbs)
    spec = _spec(name)
    assert f"[{extra}]" in spec
    for call, verb in zip(calls, verbs, strict=True):
        assert call.startswith(f"tool run --python 3.14 --from {spec} -- {name} {verb}")
        # One argument, so a directory named like an option is not parsed as one.
        assert call.endswith(f"--repository={tmp_path / 'repo'}")
    assert "exit-status=0" in (tmp_path / "out").read_text("utf-8")
    assert f"metric {name}.result=clean" in result.stdout


@posix_only
@pytest.mark.parametrize(
    ("status", "result"), [(1, "blocking"), (2, "invalid"), (3, "invalid")]
)
@pytest.mark.parametrize("name", ACTIONS)
def test_check_status_passes_through(
    tmp_path: Path, name: str, status: int, result: str
) -> None:
    """The step fails with the command's own status and names the outcome."""
    stub = f'case "$*" in *" check "*) exit {status};; esac'
    completed = _run(tmp_path, name, stub_body=stub)
    assert completed.returncode == status
    assert f"exit-status={status}" in (tmp_path / "out").read_text("utf-8")
    assert f"metric {name}.result={result}" in completed.stdout


@posix_only
def test_failed_install_is_invalid_and_skips_check(tmp_path: Path) -> None:
    """A failed digest-checked install can never reach a green check."""
    stub = 'case "$*" in *" install "*) exit 1;; esac'
    result = _run(tmp_path, "df12-duplication", stub_body=stub)
    assert result.returncode == 2
    assert "metric df12-duplication.result=install-failed" in result.stdout
    assert not any(" check " in c for c in _calls(tmp_path))


@posix_only
@pytest.mark.parametrize("name", ACTIONS)
@pytest.mark.parametrize("python", ["3", "latest", "3.14 --upgrade", ""])
def test_a_malformed_interpreter_is_refused(
    tmp_path: Path, name: str, python: str
) -> None:
    """The interpreter is a version, never a flag or a path."""
    result = _run(tmp_path, name, DF12_PYTHON=python)
    assert result.returncode == 2
    assert _calls(tmp_path) == []


@posix_only
@pytest.mark.parametrize("name", ACTIONS)
def test_a_missing_repository_is_refused(tmp_path: Path, name: str) -> None:
    """A mistyped directory cannot pass as a clean scan."""
    result = _run(tmp_path, name, DF12_REPOSITORY=str(tmp_path / "absent"))
    assert result.returncode == 2
    assert f"metric {name}.result=invalid-input" in result.stdout


@posix_only
@pytest.mark.parametrize("name", ACTIONS)
def test_missing_uv_is_a_categorised_failure(tmp_path: Path, name: str) -> None:
    """The action names the missing prerequisite."""
    result = _run(tmp_path, name, PATH="/usr/bin:/bin")
    assert result.returncode == 2
    assert f"metric {name}.result=missing-prerequisite" in result.stdout


@pytest.mark.parametrize("name", ACTIONS)
def test_every_result_is_documented(name: str) -> None:
    """The metric vocabulary in the script and in the README cannot drift apart."""
    script = _check_step(name)["run"]
    emitted = set(re.findall(rf"{name}\.result=([a-z-]+)", script))
    emitted |= set(re.findall(r'fail "[^"]*" ([a-z-]+)', script))
    readme = (ACTIONS_ROOT / name / "README.md").read_text("utf-8")
    documented = set(re.findall(r"`([a-z]+(?:-[a-z]+)*)`", readme))
    assert emitted, "the script should name its results"
    assert emitted <= documented, sorted(emitted - documented)


def test_the_fixtures_exist_for_the_workflow_lane() -> None:
    """The self-test workflow names these trees; a rename must not strand it."""
    root = ACTIONS_ROOT.parent / "df12-fixtures"
    names = {p.name for p in root.iterdir() if p.is_dir()}
    assert names == {
        "skylos-clean",
        "skylos-dead",
        "duplication-clean",
        "duplication-dup",
    }


def _env_of(step: cabc.Mapping[str, typ.Any]) -> cabc.Mapping[str, str]:
    """Return a step's env mapping."""
    return typ.cast("cabc.Mapping[str, str]", step.get("env", {}))


def test_nothing_sets_the_uv_directories() -> None:
    """Concordat UV-002: neither action assigns the uv cache or tool directory."""
    for name in ACTIONS:
        for step in _manifest(name)["runs"]["steps"]:
            assert not {"UV_CACHE_DIR", "UV_TOOL_DIR"} & set(_env_of(step))
        assert "UV_CACHE_DIR" not in _check_step(name)["run"]
