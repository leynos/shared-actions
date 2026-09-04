"""Exercise the install-mdtablefix input-validation fragment.

Each test runs the real ``Validate mdtablefix inputs`` Bash fragment, so the
rejection rules asserted here are the ones a caller meets on a runner.
"""

from __future__ import annotations

import typing as typ

import pytest
from _mdtablefix_manifest import step_by_name

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    ambient_env,
    bash_file_path,
    bash_path,
    require_posix_host,
    run_step,
)

if typ.TYPE_CHECKING:
    import subprocess
    from pathlib import Path

require_posix_host()

_VALID = {"version": "0.5.0", "binstall-version": "1.22.0", "bin-dir": "~/.local/bin"}


def _validate(
    tmp_path: Path,
    inputs: dict[str, str],
    runner_os: str = "Linux",
) -> tuple[subprocess.CompletedProcess[str], ActionContext]:
    """Run the validation fragment with ``inputs`` and return its result."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    context = ActionContext(
        inputs={**_VALID, **inputs},
        runner_os=runner_os,
        runner_arch="X64",
        action_path=bash_path(tmp_path),
    )
    environment = FragmentEnvironment(
        base_env={
            **ambient_env(),
            "HOME": bash_path(home),
            "RUNNER_OS": runner_os,
            "RUNNER_ARCH": "X64",
            "GITHUB_PATH": bash_file_path(tmp_path / "github-path"),
            "GITHUB_STEP_SUMMARY": bash_file_path(tmp_path / "step-summary"),
        },
        cwd=tmp_path,
        output_dir=tmp_path / "outputs",
    )
    step = step_by_name("Validate mdtablefix inputs")
    process = run_step(step, context, environment, "validate-output")
    return process, context


def _outputs(context: ActionContext) -> dict[str, str]:
    """Return the outputs the validation step recorded."""
    return context.step_outputs.get("validate-inputs", {})


class TestAcceptedInputs:
    """Validate what the fragment publishes for a well-formed call."""

    def test_publishes_the_resolved_paths(self, tmp_path: Path) -> None:
        """Verify the resolved bin directory and executable path."""
        process, context = _validate(tmp_path, {})

        assert process.returncode == 0, process.stderr
        outputs = _outputs(context)
        assert outputs["version"] == "0.5.0"
        assert outputs["binstall-version"] == "1.22.0"
        assert outputs["bin-dir"].endswith("/.local/bin")
        assert outputs["executable-path"] == f"{outputs['bin-dir']}/mdtablefix"

    def test_creates_the_bin_directory(self, tmp_path: Path) -> None:
        """Verify the directory exists so the caller's cache can own it."""
        _validate(tmp_path, {})

        assert (tmp_path / "home" / ".local" / "bin").is_dir()

    def test_adds_the_windows_executable_suffix(self, tmp_path: Path) -> None:
        """Verify a Windows runner's executable path carries ``.exe``.

        The action rejects Windows a step later, but the path it publishes must
        still be the one that runner would use.
        """
        _, context = _validate(tmp_path, {}, runner_os="Windows")

        assert _outputs(context)["executable-path"].endswith("/mdtablefix.exe")

    def test_accepts_an_absolute_bin_directory(self, tmp_path: Path) -> None:
        """Verify an absolute path is taken as given."""
        target = tmp_path / "tools"
        target.mkdir()
        process, context = _validate(tmp_path, {"bin-dir": bash_path(target)})

        assert process.returncode == 0, process.stderr
        assert _outputs(context)["bin-dir"] == bash_path(target)


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        pytest.param({"version": ""}, "version must be three", id="empty-version"),
        pytest.param({"version": "0.5"}, "version must be three", id="short-version"),
        pytest.param(
            {"version": "0.5.0-rc1"},
            "version must be three",
            id="prerelease-version",
        ),
        pytest.param({"version": "0.05.0"}, "version must be three", id="leading-zero"),
        pytest.param(
            {"version": "$(touch pwned)"},
            "version must be three",
            id="command-substitution",
        ),
        pytest.param(
            {"binstall-version": "1.22"},
            "binstall-version must be three",
            id="short-binstall-version",
        ),
        pytest.param(
            {"bin-dir": "relative/bin"},
            "bin-dir must be an absolute path",
            id="relative-bin-dir",
        ),
        pytest.param(
            {"bin-dir": "~/../escape"},
            "bin-dir must not contain parent-directory components",
            id="parent-bin-dir",
        ),
        pytest.param(
            {"bin-dir": "/opt/tools/a:b"},
            "bin-dir must not contain the runner PATH separator",
            id="separator-bin-dir",
        ),
        pytest.param(
            {"bin-dir": "/opt/" + "a" * 240},
            "bin-dir must be at most 240 characters",
            id="long-bin-dir",
        ),
        pytest.param(
            {"bin-dir": "~/bin\nrogue"},
            "bin-dir must not contain a carriage return or newline",
            id="newline-bin-dir",
        ),
    ],
)
def test_rejects_malformed_input(
    tmp_path: Path,
    inputs: dict[str, str],
    expected: str,
) -> None:
    """Verify each malformed input is refused with a named reason."""
    process, _ = _validate(tmp_path, inputs)

    assert process.returncode == 1
    assert "::error title=Invalid mdtablefix input::" in process.stderr
    assert expected in process.stderr
