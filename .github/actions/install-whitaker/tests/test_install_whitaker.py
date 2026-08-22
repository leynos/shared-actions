"""Verify the install-whitaker action's contracts and installation paths.

The suite exercises the composite action's shell fragment with deterministic
Cargo stubs and validates its manifest and state-dependent behaviour. Run it
with ``uv run pytest .github/actions/install-whitaker/tests/test_install_whitaker.py``.
"""

from __future__ import annotations

import os
import shutil
import string
import subprocess
import typing as typ
from dataclasses import dataclass  # noqa: ICN003 - required scenario decorator.
from itertools import product
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
_PROPERTY_TEST_SETTINGS = settings(
    derandomize=True,
    max_examples=25,
)
_VALID_INSTALLER_VERSIONS = st.lists(
    st.from_regex(r"0|[1-9][0-9]{0,2}", fullmatch=True),
    min_size=1,
    max_size=3,
).map(".".join)
_WINDOWS_RESERVED_CARGO_HOME_SEGMENTS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    },
)
_SAFE_CARGO_HOME_SEGMENTS = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=16,
).filter(
    lambda segment: segment.upper() not in _WINDOWS_RESERVED_CARGO_HOME_SEGMENTS,
)


def _load_manifest() -> dict[str, object]:
    """Load the action manifest."""
    return typ.cast(
        "dict[str, object]",
        yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8")),
    )


def _install_script() -> str:
    """Return the suite installation shell fragment."""
    manifest = _load_manifest()
    runs = manifest["runs"]
    assert isinstance(runs, dict)
    steps = typ.cast("list[dict[str, object]]", runs["steps"])
    step = next(
        (item for item in steps if item.get("name") == "Install Whitaker Dylint suite"),
        None,
    )
    assert step is not None
    script = step.get("run")
    assert isinstance(script, str)
    return script


def _input_validation_script() -> str:
    """Return the input-validation shell fragment."""
    manifest = _load_manifest()
    runs = manifest["runs"]
    assert isinstance(runs, dict)
    steps = typ.cast("list[dict[str, object]]", runs["steps"])
    step = next(
        (item for item in steps if item.get("id") == "validate-inputs"),
        None,
    )
    assert step is not None
    script = step.get("run")
    assert isinstance(script, str)
    return script


def _write_executable(path: Path, content: str) -> None:
    """Write an executable test stub."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_cargo_stub(bin_dir: Path) -> None:
    """Write a Cargo stub that records and simulates installer commands."""
    _write_executable(
        bin_dir / "cargo",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$CARGO_LOG"
if [ "${1:-}" = "binstall" ] && [ "${2:-}" = "--version" ]; then
  [ "$BINSTALL_AVAILABLE" = "true" ]
  exit
fi
if [ "${1:-}" = "binstall" ] && [ "$FAIL_BINSTALL" = "true" ]; then
  echo "cargo binstall failed while installing whitaker-installer" >&2
  exit 31
fi
if [ "${1:-}" = "install" ] && [ "$FAIL_INSTALL" = "true" ]; then
  echo "cargo install failed while installing whitaker-installer" >&2
  exit 32
fi
cat > "$FAKE_BIN_DIR/whitaker-installer" <<'INSTALLER'
#!/usr/bin/env bash
set -euo pipefail
if [ "$FAIL_INSTALLER" = "true" ]; then
  echo "whitaker-installer failed while installing the Dylint suite" >&2
  exit 33
fi
printf '%s\n' "suite installed" >> "$INSTALLER_LOG"
INSTALLER
chmod +x "$FAKE_BIN_DIR/whitaker-installer"
""",
    )


def _bash_path(bash: str, path: Path) -> str:
    """Return an existing path in the syntax understood by Bash."""
    return subprocess.run(  # noqa: S603,TID251 - Bash is resolved with shutil.which.
        [bash, "-c", 'cd -- "$1" && pwd -P', "bash", path.as_posix()],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    ).stdout.strip()


@dataclass(frozen=True)
class _InstallScenario:
    binstall_available: bool
    installer_present: bool = False
    fail_binstall: bool = False
    fail_install: bool = False
    fail_installer: bool = False
    installer_version: str = "0.2.6"
    cargo_home_name: str = "cargo-home"
    cargo_home_value: str | None = None
    cache_hit: bool = False
    conflicting_installer: bool = False


def _execute_install_script(
    bash: str,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Execute the installation fragment with the prepared environment."""
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _install_script()],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _run_input_validation(
    tmp_path: Path,
    cargo_home: str,
    installer_version: str,
) -> subprocess.CompletedProcess[str]:
    """Run the validation fragment with supplied action inputs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    output_path = tmp_path / "output"
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _input_validation_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "BASH_ENV": "",
            "CARGO_HOME_INPUT": cargo_home,
            "GITHUB_OUTPUT": (
                f"{_bash_path(bash, output_path.parent)}/{output_path.name}"
            ),
            "HOME": _bash_path(bash, home_dir),
            "INSTALLER_VERSION_INPUT": installer_version,
        },
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _run_install_script(
    tmp_path: Path,
    scenario: _InstallScenario,
) -> subprocess.CompletedProcess[str]:
    """Run the installation fragment with deterministic command stubs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    cargo_home = tmp_path / scenario.cargo_home_name
    bin_dir = cargo_home / "bin"
    bin_dir.mkdir(parents=True)
    cargo_log = tmp_path / "cargo.log"
    installer_log = tmp_path / "installer.log"
    conflict_log = tmp_path / "conflict.log"
    summary_log = tmp_path / "summary.md"
    home_dir = tmp_path / "home"
    home_dir.mkdir(exist_ok=True)
    bash_cargo_home = _bash_path(bash, cargo_home)
    bash_bin_dir = _bash_path(bash, bin_dir)
    bash_home_dir = _bash_path(bash, home_dir)
    bash_cargo_log = f"{_bash_path(bash, cargo_log.parent)}/{cargo_log.name}"
    bash_installer_log = (
        f"{_bash_path(bash, installer_log.parent)}/{installer_log.name}"
    )
    bash_summary_log = f"{_bash_path(bash, summary_log.parent)}/{summary_log.name}"
    _write_cargo_stub(bin_dir)
    original_path = "/usr/bin:/bin"
    if scenario.conflicting_installer:
        original_bin_dir = tmp_path / "original-bin"
        original_bin_dir.mkdir()
        _write_executable(
            original_bin_dir / "whitaker-installer",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "ambient installer ran" >> "$CONFLICT_LOG"
""",
        )
        original_path = f"{_bash_path(bash, original_bin_dir)}:{original_path}"
    if scenario.installer_present:
        _write_executable(
            bin_dir / "whitaker-installer",
            """#!/usr/bin/env bash
set -euo pipefail
if [ "$FAIL_INSTALLER" = "true" ]; then
  echo "whitaker-installer failed while installing the Dylint suite" >&2
  exit 33
fi
printf '%s\n' "suite installed" >> "$INSTALLER_LOG"
""",
        )

    env = {
        **os.environ,
        "PATH": original_path,
        "BASH_ENV": "",
        "CARGO_HOME": scenario.cargo_home_value or bash_cargo_home,
        "HOME": bash_home_dir,
        "BINSTALL_AVAILABLE": str(scenario.binstall_available).lower(),
        "CARGO_LOG": bash_cargo_log,
        "CONFLICT_LOG": f"{_bash_path(bash, conflict_log.parent)}/{conflict_log.name}",
        "FAIL_BINSTALL": str(scenario.fail_binstall).lower(),
        "FAIL_INSTALL": str(scenario.fail_install).lower(),
        "FAIL_INSTALLER": str(scenario.fail_installer).lower(),
        "FAKE_BIN_DIR": bash_bin_dir,
        "INSTALLER_LOG": bash_installer_log,
        "GITHUB_STEP_SUMMARY": bash_summary_log,
        "WHITAKER_INSTALLER_CACHE_HIT": str(scenario.cache_hit).lower(),
        "WHITAKER_INSTALLER_VERSION": scenario.installer_version,
    }
    return _execute_install_script(bash, tmp_path, env)


class TestManifest:
    """Validate the action manifest's declared contract."""

    def test_manifest_exposes_version_and_cache_contract(self) -> None:
        """Verify the manifest's versioned installer-cache contract."""
        manifest = _load_manifest()

        assert manifest["inputs"] == {
            "cargo-home": {
                "description": (
                    "Cargo home that stores the cached whitaker-installer binary"
                ),
                "required": False,
                "default": "~/.cargo",
            },
            "installer-version": {
                "description": "Version of whitaker-installer to install",
                "required": False,
                "default": "0.2.6",
            },
        }
        runs = manifest["runs"]
        assert isinstance(runs, dict)
        steps = typ.cast("list[dict[str, object]]", runs["steps"])
        validate_step, cache_step, install_step = steps
        assert validate_step["id"] == "validate-inputs"
        validate_env = typ.cast("dict[str, str]", validate_step["env"])
        assert validate_env == {
            "CARGO_HOME_INPUT": "${{ inputs.cargo-home }}",
            "INSTALLER_VERSION_INPUT": "${{ inputs.installer-version }}",
        }
        validate_script = typ.cast("str", validate_step["run"])
        assert "must not contain a carriage return or newline" in validate_script
        assert "must be an absolute path or start with ~/" in validate_script
        assert "without leading zeros" in validate_script

        assert cache_step["id"] == "cache-whitaker-installer"
        assert cache_step["uses"] == (
            "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
        )
        cache_config = typ.cast("dict[str, str]", cache_step["with"])
        assert (
            "${{ steps.validate-inputs.outputs.cargo-home }}/bin/whitaker-installer"
        ) in cache_config["path"]
        assert "~/.cache/cargo-binstall" in cache_config["path"]
        assert cache_config["key"] == (
            "whitaker-installer-${{ runner.os }}-${{ runner.arch }}-"
            "${{ steps.validate-inputs.outputs.installer-version }}-"
            "${{ steps.validate-inputs.outputs.cargo-home }}"
        )

        install_env = typ.cast("dict[str, str]", install_step["env"])
        assert install_env["CARGO_HOME"] == (
            "${{ steps.validate-inputs.outputs.cargo-home }}"
        )
        assert install_env["WHITAKER_INSTALLER_VERSION"] == (
            "${{ steps.validate-inputs.outputs.installer-version }}"
        )
        assert install_env["WHITAKER_INSTALLER_CACHE_HIT"] == (
            "${{ steps.cache-whitaker-installer.outputs.cache-hit }}"
        )
        install_script = typ.cast("str", install_step["run"])
        assert "title=Whitaker installer cache" in install_script
        assert "title=Whitaker installer::status=complete" in install_script
        assert "GITHUB_STEP_SUMMARY" in install_script
        assert 'CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"' in install_script

    def test_normalizes_valid_action_inputs(self, tmp_path: Path) -> None:
        """Verify validation expands the supported tilde Cargo-home form."""
        result = _run_input_validation(tmp_path, "~/.cargo", "1.2.3")

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "output").read_text(encoding="utf-8").splitlines() == [
            "cargo-home="
            f"{_bash_path(shutil.which('bash') or 'bash', tmp_path / 'home')}/.cargo",
            "installer-version=1.2.3",
        ]

    @pytest.mark.parametrize(
        ("cargo_home", "installer_version", "expected_error"),
        [
            pytest.param(
                "~/.cargo\ninjected-path",
                "1.2.3",
                "cargo-home must not contain a carriage return or newline",
                id="cargo-home-newline",
            ),
            pytest.param(
                "~/.cargo",
                "1.2.3\ninjected-command",
                "installer-version must not contain a carriage return or newline",
                id="installer-version-newline",
            ),
            pytest.param(
                "relative/.cargo",
                "1.2.3",
                "cargo-home must be an absolute path or start with ~/",
                id="relative-cargo-home",
            ),
            pytest.param(
                "~/.cargo",
                "01.2.3",
                "installer-version must be one to three numeric components "
                "without leading zeros",
                id="leading-zero-version",
            ),
            pytest.param(
                "~/.cargo",
                "1" * 129,
                "installer-version must be at most 128 characters",
                id="overlong-version",
            ),
        ],
    )
    def test_rejects_unsafe_action_inputs(
        self,
        tmp_path: Path,
        cargo_home: str,
        installer_version: str,
        expected_error: str,
    ) -> None:
        """Verify malformed action inputs fail before cache evaluation."""
        result = _run_input_validation(tmp_path, cargo_home, installer_version)

        assert result.returncode != 0
        assert expected_error in result.stderr


class TestInstallation:
    """Exercise installation, cache, and PATH precedence paths."""

    def test_installs_with_cargo_binstall_when_available(self, tmp_path: Path) -> None:
        """Verify cargo-binstall installs the pinned installer."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(binstall_available=True),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
            "binstall --version",
            "binstall --no-confirm --locked whitaker-installer@0.2.6",
        ]
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert (
            "::notice title=Whitaker installer::path=cargo-binstall version=0.2.6"
            in result.stdout
        )
        assert (
            "::notice title=Whitaker installer::status=complete version=0.2.6"
            in result.stdout
        )
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.path=cargo-binstall",
            "whitaker-installer.result=success",
        ]

    def test_falls_back_to_cargo_install(self, tmp_path: Path) -> None:
        """Verify the Cargo-install fallback when cargo-binstall is unavailable."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(binstall_available=False),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
            "binstall --version",
            "install --locked whitaker-installer --version 0.2.6",
        ]
        assert "cargo-binstall unavailable" in result.stdout
        assert (
            "::notice title=Whitaker installer::path=cargo-install version=0.2.6"
            in result.stdout
        )
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.path=cargo-install",
            "whitaker-installer.result=success",
        ]

    def test_reuses_cached_installer(self, tmp_path: Path) -> None:
        """Verify a restored installer bypasses both Cargo installation paths."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                binstall_available=False,
                installer_present=True,
                cache_hit=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert not (tmp_path / "cargo.log").exists()
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert "::notice title=Whitaker installer::path=cache version=0.2.6" in (
            result.stdout
        )
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=hit",
            "whitaker-installer.path=cache",
            "whitaker-installer.result=success",
        ]

    def test_installs_nondefault_version_into_nondefault_cargo_home(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a custom Cargo home contains the requested installer version."""
        scenario = _InstallScenario(
            binstall_available=True,
            installer_version="9.9.9",
            cargo_home_name="custom-cargo-home",
        )
        result = _run_install_script(tmp_path, scenario)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "custom-cargo-home" / "bin" / "whitaker-installer").is_file()
        assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
            "binstall --version",
            "binstall --no-confirm --locked whitaker-installer@9.9.9",
        ]
        assert "::notice title=Whitaker installer cache::hit=false version=9.9.9" in (
            result.stdout
        )

    def test_expands_tilde_cargo_home_before_prepending_path(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the Cargo-home installer overrides an ambient PATH installer."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                binstall_available=True,
                installer_present=True,
                cargo_home_name="home/.cargo",
                cargo_home_value="~/.cargo",
                conflicting_installer=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "home" / ".cargo" / "bin" / "whitaker-installer").is_file()
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert not (tmp_path / "cargo.log").exists()
        assert not (tmp_path / "conflict.log").exists()

    def test_installs_into_expanded_tilde_cargo_home_before_ambient_path(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify expanded Cargo home wins over an ambient PATH installer."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                binstall_available=True,
                cargo_home_name="home/.cargo",
                cargo_home_value="~/.cargo",
                conflicting_installer=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "home" / ".cargo" / "bin" / "whitaker-installer").is_file()
        assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
            "binstall --version",
            "binstall --no-confirm --locked whitaker-installer@0.2.6",
        ]
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert not (tmp_path / "conflict.log").exists()


class TestProperties:
    """Check generated installer versions and Cargo-home forms."""

    @_PROPERTY_TEST_SETTINGS
    @given(installer_version=_VALID_INSTALLER_VERSIONS)
    def test_installs_generated_valid_installer_versions(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        installer_version: str,
    ) -> None:
        """Verify cargo-binstall accepts each generated Cargo-compatible version."""
        example_path = tmp_path_factory.mktemp("installer-version-")
        result = _run_install_script(
            example_path,
            _InstallScenario(
                binstall_available=True,
                installer_version=installer_version,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (example_path / "cargo.log").read_text(
            encoding="utf-8"
        ).splitlines() == [
            "binstall --version",
            f"binstall --no-confirm --locked whitaker-installer@{installer_version}",
        ]
        assert (
            "::notice title=Whitaker installer::status=complete "
            f"version={installer_version}"
        ) in result.stdout

    @pytest.mark.parametrize("cargo_home_form", ["absolute", "tilde"])
    @_PROPERTY_TEST_SETTINGS
    @given(segment=_SAFE_CARGO_HOME_SEGMENTS)
    def test_reuses_cached_installer_for_supported_cargo_home_forms(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        cargo_home_form: str,
        segment: str,
    ) -> None:
        """Verify supported Cargo-home forms select the cached installer first."""
        example_path = tmp_path_factory.mktemp(f"{cargo_home_form}-cargo-home-")
        if cargo_home_form == "absolute":
            cargo_home_name = f"absolute-cargo/{segment}"
            cargo_home_value = None
        else:
            cargo_home_name = f"home/.cargo/{segment}"
            cargo_home_value = f"~/.cargo/{segment}"

        result = _run_install_script(
            example_path,
            _InstallScenario(
                binstall_available=False,
                installer_present=True,
                cargo_home_name=cargo_home_name,
                cargo_home_value=cargo_home_value,
                conflicting_installer=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (example_path / cargo_home_name / "bin" / "whitaker-installer").is_file()
        assert (example_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert not (example_path / "conflict.log").exists()
        assert not (example_path / "cargo.log").exists()


def _scenario_should_fail(scenario: _InstallScenario) -> bool:
    """Return whether the selected installer path is expected to fail."""
    if scenario.installer_present:
        return scenario.fail_installer
    if scenario.binstall_available:
        return scenario.fail_binstall or scenario.fail_installer
    return scenario.fail_install or scenario.fail_installer


class TestScenarioMatrix:
    """Check every bounded installer-state combination."""

    @pytest.mark.parametrize(
        "scenario",
        tuple(
            _InstallScenario(
                binstall_available=binstall_available,
                installer_present=installer_present,
                fail_binstall=fail_binstall,
                fail_install=fail_install,
                fail_installer=fail_installer,
            )
            for (
                binstall_available,
                installer_present,
                fail_binstall,
                fail_install,
                fail_installer,
            ) in product((False, True), repeat=5)
        ),
    )
    def test_install_scenario_matrix(
        self,
        tmp_path: Path,
        scenario: _InstallScenario,
    ) -> None:
        """Exhaustively verify the finite installer-state failure contract."""
        result = _run_install_script(tmp_path, scenario)

        assert (result.returncode != 0) is _scenario_should_fail(scenario), (
            result.stderr
        )


class TestFailures:
    """Check actionable diagnostics for selected installation failures."""

    @pytest.mark.parametrize(
        ("scenario", "expected_error"),
        [
            pytest.param(
                _InstallScenario(
                    binstall_available=True,
                    fail_binstall=True,
                ),
                "cargo binstall failed while installing whitaker-installer",
                id="cargo-binstall",
            ),
            pytest.param(
                _InstallScenario(
                    binstall_available=False,
                    fail_install=True,
                ),
                "cargo install failed while installing whitaker-installer",
                id="cargo-install",
            ),
            pytest.param(
                _InstallScenario(
                    binstall_available=True,
                    fail_installer=True,
                ),
                "whitaker-installer failed while installing the Dylint suite",
                id="whitaker-installer",
            ),
        ],
    )
    def test_reports_install_failure(
        self,
        tmp_path: Path,
        scenario: _InstallScenario,
        expected_error: str,
    ) -> None:
        """Verify actionable errors for each selected installer failure path."""
        result = _run_install_script(tmp_path, scenario)

        assert result.returncode != 0
        assert expected_error in result.stderr
        assert (
            f"::error title=Whitaker installer failed::exit-code={result.returncode} "
            "version=0.2.6"
        ) in result.stderr
        assert "whitaker-installer.failure=command" in (
            tmp_path / "summary.md"
        ).read_text(encoding="utf-8")
