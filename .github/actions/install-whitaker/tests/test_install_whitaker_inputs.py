"""Exercise the install-whitaker action's input-validation fragment.

Every case runs the real Bash fragment declared by the ``validate-inputs``
step, so the normalisation and rejection rules are asserted against the shipped
script rather than a paraphrase of it.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest
from _action_manifest import step_by_id
from _fragment_runner import (
    ActionContext,
    FragmentEnvironment,
    ambient_env,
    bash_path,
    require_posix_host,
    run_step,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_PAYLOAD_SHA256 = "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"

require_posix_host()


@dc.dataclass(frozen=True)
class ValidationInputs:
    """Describe the action inputs supplied to the validation fragment."""

    cargo_home: str
    installer_version: str
    cache_provider: str = "github"
    runner_os: str = "Linux"
    installer_sha256: str = ""


@dc.dataclass(frozen=True)
class InvalidInputCase:
    """Describe one rejected action-input contract case."""

    cargo_home: str
    installer_version: str
    expected_error: str


@dc.dataclass(frozen=True)
class ValidationRun:
    """Expose the outcome and resolved outputs of one validation run."""

    returncode: int
    stderr: str
    outputs: dict[str, str]
    home: Path


def run_validation(tmp_path: Path, inputs: ValidationInputs) -> ValidationRun:
    """Run the validation fragment with the supplied action inputs."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    context = ActionContext(
        inputs={
            "cache-provider": inputs.cache_provider,
            "cargo-home": inputs.cargo_home,
            "installer-sha256": inputs.installer_sha256,
            "installer-version": inputs.installer_version,
        },
        runner_os=inputs.runner_os,
        runner_arch="X64",
        action_path=bash_path(tmp_path),
    )
    process = run_step(
        step_by_id("validate-inputs"),
        context,
        FragmentEnvironment(
            base_env={
                **ambient_env(),
                "HOME": bash_path(home),
                "RUNNER_OS": inputs.runner_os,
                "RUNNER_TEMP": bash_path(tmp_path),
            },
            cwd=tmp_path,
            output_dir=tmp_path,
        ),
        "output",
    )
    return ValidationRun(
        returncode=process.returncode,
        stderr=process.stderr,
        outputs=context.step_outputs.get("validate-inputs", {}),
        home=home,
    )


class TestNormalisation:
    """Check the outputs the validation fragment publishes."""

    def test_expands_a_tilde_cargo_home(self, tmp_path: Path) -> None:
        """Verify the supported tilde form resolves against ``HOME``."""
        run = run_validation(tmp_path, ValidationInputs("~/.cargo", "1.2.3"))

        assert run.returncode == 0, run.stderr
        home = bash_path(run.home)
        assert run.outputs == {
            "cargo-home": f"{home}/.cargo",
            "installer-path": f"{home}/.cargo/bin/whitaker-installer",
            "installer-version-path": (
                f"{home}/.cargo/bin/.whitaker-installer-version"
            ),
            "installer-version": "1.2.3",
            "installer-sha256": "",
        }

    def test_selects_the_windows_executable_suffix(self, tmp_path: Path) -> None:
        """Verify Windows caches and executes the native installer name."""
        run = run_validation(
            tmp_path,
            ValidationInputs("~/.cargo", "1.2.3", runner_os="Windows"),
        )

        assert run.returncode == 0, run.stderr
        assert run.outputs["installer-path"].endswith("/whitaker-installer.exe")

    def test_lowercases_a_supplied_digest(self, tmp_path: Path) -> None:
        """Verify an uppercase digest input is normalised for comparison."""
        run = run_validation(
            tmp_path,
            ValidationInputs(
                "~/.cargo",
                "1.2.3",
                installer_sha256=_PAYLOAD_SHA256.upper(),
            ),
        )

        assert run.returncode == 0, run.stderr
        assert run.outputs["installer-sha256"] == _PAYLOAD_SHA256


class TestRejections:
    """Check that malformed inputs fail before any cache or download."""

    def test_rejects_an_unset_runner_temp(self, tmp_path: Path) -> None:
        """Verify a missing staging directory fails before any download."""
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        context = ActionContext(
            inputs={
                "cache-provider": "github",
                "cargo-home": "~/.cargo",
                "installer-sha256": "",
                "installer-version": "1.2.3",
            },
            runner_os="Linux",
            runner_arch="X64",
            action_path=bash_path(tmp_path),
        )
        base_env = {
            key: value for key, value in ambient_env().items() if key != "RUNNER_TEMP"
        }
        process = run_step(
            step_by_id("validate-inputs"),
            context,
            FragmentEnvironment(
                base_env={
                    **base_env,
                    "HOME": bash_path(home),
                    "RUNNER_OS": "Linux",
                },
                cwd=tmp_path,
                output_dir=tmp_path,
            ),
            "output",
        )

        assert process.returncode != 0
        assert "RUNNER_TEMP must name a writable staging directory" in process.stderr

    def test_rejects_an_unknown_cache_provider(self, tmp_path: Path) -> None:
        """Verify cache ownership fails closed before cache evaluation."""
        run = run_validation(
            tmp_path,
            ValidationInputs("~/.cargo", "1.2.3", cache_provider="namespace"),
        )

        assert run.returncode != 0
        assert "cache-provider must be github or external" in run.stderr

    @pytest.mark.parametrize(
        ("case", "runner_os"),
        [
            pytest.param(
                InvalidInputCase(
                    "~/.cargo\ninjected-path",
                    "1.2.3",
                    "cargo-home must not contain a carriage return or newline",
                ),
                "Linux",
                id="cargo-home-newline",
            ),
            pytest.param(
                InvalidInputCase(
                    "~/.cargo",
                    "1.2.3\ninjected-command",
                    "installer-version must not contain a carriage return or newline",
                ),
                "Linux",
                id="installer-version-newline",
            ),
            pytest.param(
                InvalidInputCase(
                    "relative/.cargo",
                    "1.2.3",
                    "cargo-home must be an absolute path or start with ~/",
                ),
                "Linux",
                id="relative-cargo-home",
            ),
            pytest.param(
                InvalidInputCase(
                    "/cargo-home:unsafe",
                    "1.2.3",
                    "cargo-home must not contain the runner PATH separator",
                ),
                "Linux",
                id="linux-cargo-home-path-separator",
            ),
            pytest.param(
                InvalidInputCase(
                    "~/.cargo;unsafe",
                    "1.2.3",
                    "cargo-home must not contain the runner PATH separator",
                ),
                "Windows",
                id="windows-cargo-home-path-separator",
            ),
            pytest.param(
                InvalidInputCase(
                    "~/.cargo",
                    "01.2.3",
                    "installer-version must be one to three numeric components "
                    "without leading zeros",
                ),
                "Linux",
                id="leading-zero-version",
            ),
            pytest.param(
                InvalidInputCase(
                    "~/.cargo",
                    "1" * 129,
                    "installer-version must be at most 128 characters",
                ),
                "Linux",
                id="overlong-version",
            ),
        ],
    )
    def test_rejects_unsafe_inputs(
        self,
        tmp_path: Path,
        case: InvalidInputCase,
        runner_os: str,
    ) -> None:
        """Verify malformed action inputs fail before cache evaluation."""
        run = run_validation(
            tmp_path,
            ValidationInputs(
                case.cargo_home,
                case.installer_version,
                runner_os=runner_os,
            ),
        )

        assert run.returncode != 0
        assert case.expected_error in run.stderr

    @pytest.mark.parametrize(
        "installer_sha256",
        ["not-a-digest", _PAYLOAD_SHA256[:-1], f"{_PAYLOAD_SHA256}0"],
    )
    def test_rejects_a_malformed_digest(
        self,
        tmp_path: Path,
        installer_sha256: str,
    ) -> None:
        """Verify a malformed digest input fails before any download."""
        run = run_validation(
            tmp_path,
            ValidationInputs("~/.cargo", "1.2.3", installer_sha256=installer_sha256),
        )

        assert run.returncode != 0
        assert "installer-sha256 must be 64 hexadecimal characters" in run.stderr
