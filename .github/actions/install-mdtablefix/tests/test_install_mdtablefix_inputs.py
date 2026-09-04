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

_VALID = {"version": "0.5.1", "binstall-version": "1.22.0", "bin-dir": "~/.local/bin"}


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

        assert process.returncode == 0, (
            f"validation rejected a well-formed call: {process.stderr}"
        )
        outputs = _outputs(context)
        assert outputs["version"] == "0.5.1", f"unexpected outputs: {outputs}"
        assert outputs["binstall-version"] == "1.22.0", f"unexpected outputs: {outputs}"
        assert outputs["bin-dir"].endswith("/.local/bin"), (
            f"the default bin-dir was not expanded against HOME: {outputs}"
        )
        assert outputs["executable-path"] == f"{outputs['bin-dir']}/mdtablefix", (
            f"the executable path must sit inside bin-dir: {outputs}"
        )

    def test_creates_the_bin_directory(self, tmp_path: Path) -> None:
        """Verify the directory exists so the caller's cache can own it."""
        process, _ = _validate(tmp_path, {})

        assert (tmp_path / "home" / ".local" / "bin").is_dir(), (
            f"bin-dir was not created: {process.stderr}"
        )

    def test_adds_the_windows_executable_suffix(self, tmp_path: Path) -> None:
        """Verify a Windows runner's executable path carries ``.exe``."""
        _, context = _validate(tmp_path, {}, runner_os="Windows")

        outputs = _outputs(context)
        assert outputs["executable-path"].endswith("/mdtablefix.exe"), (
            f"a Windows runner needs the .exe suffix: {outputs}"
        )

    def test_accepts_an_absolute_bin_directory(self, tmp_path: Path) -> None:
        """Verify an absolute path is taken as given."""
        target = tmp_path / "tools"
        target.mkdir()
        process, context = _validate(tmp_path, {"bin-dir": bash_path(target)})

        assert process.returncode == 0, (
            f"validation rejected an absolute bin-dir: {process.stderr}"
        )
        assert _outputs(context)["bin-dir"] == bash_path(target), (
            f"an absolute bin-dir must be taken as given: {_outputs(context)}"
        )

    def test_rejects_a_native_windows_path_without_cygpath(
        self, tmp_path: Path
    ) -> None:
        """Verify a native Windows path is refused when it cannot be converted.

        `${{ runner.temp }}` is the natural value for a Windows caller and
        arrives as a drive-letter path, which Git Bash does not consider
        absolute. The action converts it with `cygpath`, and this host has no
        `cygpath`, so the conversion is refused with a message naming the cause
        rather than the generic "must be an absolute path", which would send a
        reader looking for the wrong problem.

        The successful conversion is exercised on a real Windows runner by the
        `Test install-mdtablefix` workflow; nothing on a POSIX host can stand
        in for `cygpath` without testing the stand-in.
        """
        process, _ = _validate(
            tmp_path, {"bin-dir": r"D:\a\_temp\bin"}, runner_os="Windows"
        )

        assert process.returncode == 1, (
            f"a native Windows path should not validate here: {process.stderr}"
        )
        assert "cygpath" in process.stderr, (
            f"the failure should name the missing converter: {process.stderr}"
        )


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        pytest.param({"version": ""}, "version must be three", id="empty-version"),
        pytest.param({"version": "0.5"}, "version must be three", id="short-version"),
        pytest.param(
            {"version": "0.5.1-rc1"},
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

    assert process.returncode == 1, (
        f"{inputs} was accepted; stderr was {process.stderr!r}"
    )
    assert "::error title=Invalid mdtablefix input::" in process.stderr, (
        f"a rejection must be annotated; stderr was {process.stderr!r}"
    )
    assert expected in process.stderr, (
        f"expected {expected!r} in the rejection; stderr was {process.stderr!r}"
    )
