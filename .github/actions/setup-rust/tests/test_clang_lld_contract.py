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

INSTALL_STEP = "Install clang and lld"
SKIP_STEP = "Skip clang and lld off Linux"
VALIDATE_STEP = "Validate install-clang-lld"


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


def test_the_install_step_updates_then_installs_with_apt() -> None:
    """The step refreshes the package index before installing, non-interactively."""
    run = str(get_step(INSTALL_STEP)["run"])
    lines = [line.strip() for line in run.splitlines()]

    assert "export DEBIAN_FRONTEND=noninteractive" in lines
    assert "sudo apt-get update" in lines
    assert "sudo apt-get install --yes --no-install-recommends clang lld" in lines


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


def _write_apt_stub(stubs_dir: Path, bash: str) -> None:
    """Write a no-op ``sudo`` stub that swallows ``apt-get`` calls.

    The real step shells out to ``sudo apt-get``; the stub records nothing
    beyond letting the script proceed, so the behavioural tests below exercise
    only the PATH verification that follows the install.
    """
    stubs_dir.mkdir(parents=True, exist_ok=True)
    sudo = stubs_dir / "sudo"
    # An absolute shebang: the restricted PATH cannot resolve ``env bash``.
    sudo.write_text(f"#!{bash}\nexit 0\n", encoding="utf-8")
    sudo.chmod(0o755)


def _run_install_step(
    tmp_path: Path, *, tool_path: Path | None
) -> subprocess.CompletedProcess[str]:
    """Run the install step's fragment with a stubbed ``sudo`` on PATH.

    PATH is set to exactly the stub directory, plus *tool_path* when given,
    with the host's own PATH deliberately excluded. A host that happens to
    ship a real ``clang``/``ld.lld`` (common on Linux dev images) would
    otherwise make the "missing" case pass only by accident.

    When *tool_path* is given, it is prepended ahead of the stub directory so
    stand-in ``clang``/``ld.lld`` binaries are found; otherwise only the stub
    directory is on PATH, so neither is found and the step must fail.
    """
    bash = requires_bash()
    stubs_dir = tmp_path / "stubs"
    _write_apt_stub(stubs_dir, bash)
    github_output = tmp_path / "github-output"
    github_output.touch()
    path_entries = [str(stubs_dir)]
    if tool_path is not None:
        path_entries.insert(0, str(tool_path))
    env = {
        **os.environ,
        "PATH": os.pathsep.join(path_entries),
        "GITHUB_OUTPUT": str(github_output),
    }
    run_script = str(get_step(INSTALL_STEP)["run"])
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", run_script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_install_step_reports_installed_once_both_tools_are_on_path(
    tmp_path: Path,
) -> None:
    """A successful apt install, reflected on PATH, reports ``installed``."""
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    for name in ("clang", "ld.lld"):
        stub = tool_dir / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

    result = _run_install_step(tmp_path, tool_path=tool_dir)

    assert result.returncode == 0, result.stderr
    github_output = (tmp_path / "github-output").read_text(encoding="utf-8")
    assert "status=installed" in github_output.splitlines()


def test_the_install_step_fails_clearly_when_a_tool_is_still_missing(
    tmp_path: Path,
) -> None:
    """A PATH missing ``clang`` or ``ld.lld`` after install fails with ``::error::``."""
    result = _run_install_step(tmp_path, tool_path=None)

    assert result.returncode != 0
    assert "::error title=setup-rust clang-lld::missing on PATH" in result.stderr
    assert "clang" in result.stderr
    assert "ld.lld" in result.stderr
    github_output = (tmp_path / "github-output").read_text(encoding="utf-8")
    assert "status=installed" not in github_output
