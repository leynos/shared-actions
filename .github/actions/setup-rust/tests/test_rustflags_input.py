"""Tests for the setup-rust rustflags input and its validation.

The input is forwarded to a pinned third-party toolchain action that writes it
to ``GITHUB_ENV`` as a plain assignment, so these cover both the forwarding and
the guard that stops a line break turning into further environment entries.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, load_steps, requires_bash

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path


def test_rustflags_input_defaults_to_deny_warnings() -> None:
    """The rustflags input must exist and keep the historical default."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    rustflags_input = manifest["inputs"]["rustflags"]
    assert rustflags_input.get("required", False) is False, (
        "rustflags must stay optional so existing callers need no change"
    )
    assert rustflags_input.get("default") == "-D warnings", (
        "the default must preserve the historical -D warnings behaviour; "
        f"got {rustflags_input.get('default')!r}"
    )


@pytest.mark.parametrize(
    "step_name",
    [
        "Install rust (explicit toolchain)",
        "Install rust (rust-toolchain file)",
        "Install rust (stable default)",
    ],
)
def test_install_steps_forward_rustflags(step_name: str) -> None:
    """Every toolchain install step must forward the rustflags input."""
    step = get_step(step_name)
    with_block = step.get("with")
    assert isinstance(with_block, dict), f"Step has no with block: {step_name}"
    assert with_block.get("rustflags") == "${{ inputs.rustflags }}", (
        f"{step_name} must forward the rustflags input to setup-rust-toolchain, "
        f"otherwise it re-exports the -D warnings default; got "
        f"{with_block.get('rustflags')!r}"
    )


def _validate_rustflags_run_script() -> str:
    """Return the rustflags validation step's shell script."""
    run_script = get_step("Validate rustflags").get("run")
    assert isinstance(run_script, str), "Validate rustflags step has no run script"
    return run_script


def _run_validate_rustflags(
    tmp_path: Path, rustflags: str
) -> subprocess.CompletedProcess[str]:
    """Run the rustflags validation fragment against a candidate value."""
    bash = requires_bash()
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _validate_rustflags_run_script()],
        cwd=tmp_path,
        env={**os.environ, "SR_RUSTFLAGS": rustflags},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_validate_rustflags_precedes_the_install_steps() -> None:
    """Validation must run before any step forwards the value."""
    names = [step.get("name") for step in load_steps()]
    assert names.index("Validate rustflags") < names.index(
        "Install rust (explicit toolchain)"
    ), f"validation must precede the install steps; order was {names}"


@pytest.mark.parametrize(
    "rustflags",
    ["-D warnings", "-D warnings -C debuginfo=0", ""],
    ids=["default", "extra-flag", "empty"],
)
def test_validate_rustflags_accepts_single_line_values(
    tmp_path: Path, rustflags: str
) -> None:
    """Ordinary single-line values pass validation untouched."""
    result = _run_validate_rustflags(tmp_path, rustflags)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r\n", "\r"],
    ids=["lf", "crlf", "cr"],
)
def test_validate_rustflags_rejects_line_breaks(tmp_path: Path, separator: str) -> None:
    """A line break is rejected before it can reach the nested action.

    The pinned setup-rust-toolchain writes the value as a plain
    ``RUSTFLAGS=<value>`` line, so a line break would append further
    environment-file entries and set variables the caller never asked for.
    """
    payload = f"-D warnings{separator}SR_INJECTED=1"
    result = _run_validate_rustflags(tmp_path, payload)

    assert result.returncode != 0, (
        f"a line break must fail the step; wrote {result.stdout!r}"
    )
    assert "must not contain line breaks" in result.stderr, (
        f"expected the rejection diagnostic; got {result.stderr!r}"
    )
    assert "SR_INJECTED" not in result.stderr, (
        f"the rejected value must not be echoed; got {result.stderr!r}"
    )


def test_injected_rustflags_cannot_reach_the_environment_file(tmp_path: Path) -> None:
    """Validation stops the payload the nested action would have written.

    This pins the mitigation to the sink it protects: the nested action's
    ``echo "RUSTFLAGS=$NEW_RUSTFLAGS" >> $GITHUB_ENV`` would turn the second
    line into its own entry, so the guard must reject the value first.
    """
    payload = "-D warnings\nSR_INJECTED=1"
    github_env = tmp_path / "github-env"
    github_env.write_text("", encoding="utf-8")

    guard = _run_validate_rustflags(tmp_path, payload)
    assert guard.returncode != 0, "the guard must reject the payload"

    # Show what the guard prevents: the nested action's write, run on the same
    # payload, does create a second entry.
    bash = requires_bash()
    subprocess.run(  # noqa: S603,TID251 - reproduce the nested action's sink.
        [bash, "-c", 'echo "RUSTFLAGS=$NEW_RUSTFLAGS" >> "$GITHUB_ENV"'],
        cwd=tmp_path,
        env={
            **os.environ,
            "NEW_RUSTFLAGS": payload,
            "GITHUB_ENV": github_env.as_posix(),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    entries = [
        line.split("=", 1)[0]
        for line in github_env.read_text(encoding="utf-8").splitlines()
        if "=" in line
    ]
    assert entries == ["RUSTFLAGS", "SR_INJECTED"], (
        "the sink is expected to be injectable, which is why the guard exists; "
        f"got {entries}"
    )
