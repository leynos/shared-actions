"""Contracts for how setup-rust wires its optional clang/lld install.

Mirrors `test_mold_contract.py`'s shape: the install step runs only on Linux
and only when asked, the off-Linux arm is a notice rather than a failure, and
the action sets no linker flag. Unlike mold, nothing here is pinned or
digest-verified: `clang` and `lld` come from the runner's own Ubuntu archive,
so the install step's only extra duty is to verify both land on `PATH`
afterwards and fail clearly if either does not.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

if typ.TYPE_CHECKING:
    from pathlib import Path

    from conftest import InstallStepRunner

INSTALL_STEP = "Install clang and lld"
SKIP_STEP = "Skip clang and lld off Linux"
VALIDATE_STEP = "Validate install-clang-lld"

#: The whole metric vocabulary: bounded, with no path or package output in it.
ALLOWED_METRICS = frozenset(
    {
        "metric setup-rust.clang-lld=installed",
        "metric setup-rust.clang-lld=skipped",
        "metric setup-rust.clang-lld.seconds=lt5s",
        "metric setup-rust.clang-lld.seconds=lt30s",
        "metric setup-rust.clang-lld.seconds=lt120s",
        "metric setup-rust.clang-lld.seconds=ge120s",
        "metric setup-rust.clang-lld.failure=apt-update",
        "metric setup-rust.clang-lld.failure=apt-install",
        "metric setup-rust.clang-lld.failure=missing-tool",
    }
)


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed setup-rust manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("step_name", "guard"),
    [
        pytest.param(
            INSTALL_STEP,
            "${{ inputs.install-clang-lld == 'true' && runner.os == 'Linux' }}",
            id="install",
        ),
        pytest.param(
            SKIP_STEP,
            "${{ inputs.install-clang-lld == 'true' && runner.os != 'Linux' }}",
            id="skip",
        ),
    ],
)
def test_each_clang_lld_arm_runs_only_when_asked_on_its_platform(
    step_name: str, guard: str
) -> None:
    """The two arms partition the runners, and neither runs unless asked.

    Compared whole: a guard with an extra ``|| ...`` or a dropped term would
    still contain either term.
    """
    assert " ".join(str(get_step(step_name)["if"]).split()) == guard


def test_the_install_step_runs_apt_non_interactively() -> None:
    """The step exports ``DEBIAN_FRONTEND`` and installs only the two packages."""
    run = str(get_step(INSTALL_STEP)["run"])
    lines = [line.strip() for line in run.splitlines()]

    assert "export DEBIAN_FRONTEND=noninteractive" in lines
    assert "sudo apt-get install --yes --no-install-recommends clang lld \\" in lines


def test_the_off_linux_arm_notices_and_never_fails() -> None:
    """A matrix passes ``install-clang-lld`` unconditionally; macOS/Windows skip."""
    run = str(get_step(SKIP_STEP)["run"])

    assert run.splitlines()[0].startswith('echo "::notice title=setup-rust clang-lld::')
    assert 'echo "status=skipped" >> "$GITHUB_OUTPUT"' in run.splitlines()
    assert "exit" not in run


def test_the_outputs_report_both_arms() -> None:
    """``clang-lld-status`` reads whichever arm ran."""
    outputs = _manifest()["outputs"]

    assert outputs["clang-lld-status"]["value"] == (
        "${{ steps.clang-lld.outputs.status || steps.clang-lld-skip.outputs.status }}"
    )


def test_the_default_is_off() -> None:
    """A caller must opt in; the action never installs clang/lld unasked."""
    inputs = _manifest()["inputs"]

    assert inputs["install-clang-lld"]["default"] == "false"


@pytest.mark.parametrize(
    ("value", "expected_status"),
    [
        pytest.param("true", 0, id="true"),
        pytest.param("false", 0, id="false"),
        pytest.param("yes", 1, id="yes"),
        pytest.param("True", 1, id="capitalised"),
        pytest.param("", 1, id="empty"),
    ],
)
def test_install_clang_lld_accepts_only_true_or_false(
    value: str, expected_status: int
) -> None:
    """A mistyped value fails the job instead of silently skipping the install."""
    step = get_step(VALIDATE_STEP)
    assert step["env"] == {"SR_INSTALL_CLANG_LLD": "${{ inputs.install-clang-lld }}"}
    bash = requires_bash()

    result = subprocess.run(  # noqa: S603, TID251 - runs the action's own fragment.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        env={**os.environ, "SR_INSTALL_CLANG_LLD": value},
        text=True,
    )

    assert result.returncode == expected_status
    assert ("install-clang-lld must be true or false" in result.stderr) == bool(
        expected_status
    )


def _metrics(stdout: str) -> set[str]:
    """Return the ``metric setup-rust.clang-lld`` lines in *stdout*."""
    return {
        line
        for line in stdout.splitlines()
        if line.startswith("metric setup-rust.clang-lld")
    }


def test_the_install_step_reports_installed_once_both_tools_are_on_path(
    run_install_step: InstallStepRunner,
) -> None:
    """A successful apt install, reflected on PATH, reports ``installed``."""
    run = run_install_step()

    assert run.result.returncode == 0, run.result.stderr
    assert "status=installed" in run.github_output
    assert "metric setup-rust.clang-lld=installed" in run.result.stdout.splitlines()
    assert _metrics(run.result.stdout) <= ALLOWED_METRICS
    assert not any(".failure=" in line for line in run.result.stdout.splitlines())


def test_the_install_step_refreshes_the_index_before_installing(
    run_install_step: InstallStepRunner,
) -> None:
    """The recorded ``sudo`` calls are ``apt-get update`` then ``apt-get install``."""
    run = run_install_step()

    assert run.result.returncode == 0, run.result.stderr
    assert run.sudo_calls == [
        "apt-get update",
        "apt-get install --yes --no-install-recommends clang lld",
    ]


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        pytest.param((), ("clang", "ld.lld"), id="neither"),
        pytest.param(("clang",), ("ld.lld",), id="only-clang"),
        pytest.param(("ld.lld",), ("clang",), id="only-ld-lld"),
    ],
)
def test_the_install_step_fails_naming_each_missing_tool(
    run_install_step: InstallStepRunner,
    present: tuple[str, ...],
    missing: tuple[str, ...],
) -> None:
    """Either tool absent after install fails, names exactly the absent tools."""
    run = run_install_step(present=present)

    assert run.result.returncode != 0
    error = next(
        line
        for line in run.result.stderr.splitlines()
        if line.startswith("::error title=setup-rust clang-lld::missing on PATH")
    )
    reported = error.split("install: ", 1)[1].split()
    assert reported == list(missing)
    assert "status=installed" not in run.github_output
    assert "metric setup-rust.clang-lld=installed" not in run.result.stdout
    assert "metric setup-rust.clang-lld.failure=missing-tool" in run.result.stdout


@pytest.mark.parametrize("failing", ["update", "install"])
def test_an_apt_failure_is_reported_with_its_own_category(
    run_install_step: InstallStepRunner, failing: str
) -> None:
    """A failed ``apt-get`` call fails the step even when the tools are on PATH.

    The failure names the operation in its annotation and metric, and neither
    the installed status nor the installed metric is emitted.
    """
    run = run_install_step(fail_on=failing)

    assert run.result.returncode == 1
    assert f"metric setup-rust.clang-lld.failure=apt-{failing}" in run.result.stdout
    assert f"::error title=setup-rust clang-lld::apt-get {failing}" in run.result.stderr
    assert "metric setup-rust.clang-lld=installed" not in run.result.stdout
    assert _metrics(run.result.stdout) <= ALLOWED_METRICS
    assert "status=installed" not in run.github_output


def test_the_off_linux_arm_reports_skipped_and_exits_zero(tmp_path: Path) -> None:
    """Executed, the skip fragment notices, writes ``status=skipped`` and succeeds."""
    github_output = tmp_path / "github-output"
    github_output.touch()

    result = subprocess.run(  # noqa: S603, TID251 - runs the action's own fragment.
        [requires_bash(), "-c", str(get_step(SKIP_STEP)["run"])],
        check=False,
        capture_output=True,
        env={**os.environ, "RUNNER_OS": "macOS", "GITHUB_OUTPUT": str(github_output)},
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "::notice title=setup-rust clang-lld::" in result.stdout
    assert "skipped on macOS" in result.stdout
    assert _metrics(result.stdout) == {"metric setup-rust.clang-lld=skipped"}
    assert github_output.read_text(encoding="utf-8").splitlines() == ["status=skipped"]
