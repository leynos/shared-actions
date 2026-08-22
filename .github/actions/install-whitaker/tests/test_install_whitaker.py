"""Contract tests for the install-whitaker composite action."""

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
    st.text(alphabet=string.digits, min_size=1, max_size=3),
    min_size=1,
    max_size=4,
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
    result = subprocess.run(  # noqa: S603,TID251 - Bash is resolved with shutil.which.
        [bash, "-c", 'cd -- "$1" && pwd -P', "bash", path.as_posix()],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


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
    home_dir = tmp_path / "home"
    home_dir.mkdir(exist_ok=True)
    bash_cargo_home = _bash_path(bash, cargo_home)
    bash_bin_dir = _bash_path(bash, bin_dir)
    bash_home_dir = _bash_path(bash, home_dir)
    bash_cargo_log = f"{_bash_path(bash, cargo_log.parent)}/{cargo_log.name}"
    bash_installer_log = (
        f"{_bash_path(bash, installer_log.parent)}/{installer_log.name}"
    )
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
        "WHITAKER_INSTALLER_CACHE_HIT": "false",
        "WHITAKER_INSTALLER_VERSION": scenario.installer_version,
    }
    return _execute_install_script(bash, tmp_path, env)


def test_manifest_exposes_version_and_cache_contract() -> None:
    """Verify the manifest's versioned installer-cache contract.

    Examples
    --------
    Run this contract check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k manifest_exposes_version_and_cache_contract

    Returns
    -------
    None
        Passes when the inputs, cache key and paths, environment mapping, and
        observable installer lifecycle annotations remain aligned.
    """
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
    cache_step = steps[0]
    assert cache_step["id"] == "cache-whitaker-installer"
    assert cache_step["uses"] == (
        "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    )
    cache_config = typ.cast("dict[str, str]", cache_step["with"])
    assert "${{ inputs.cargo-home }}/bin/whitaker-installer" in cache_config["path"]
    assert "~/.cache/cargo-binstall" in cache_config["path"]
    assert cache_config["key"] == (
        "whitaker-installer-${{ runner.os }}-${{ runner.arch }}-"
        "${{ inputs.installer-version }}"
    )
    install_step = steps[1]
    install_env = typ.cast("dict[str, str]", install_step["env"])
    assert install_env["CARGO_HOME"] == "${{ inputs.cargo-home }}"
    assert install_env["WHITAKER_INSTALLER_VERSION"] == (
        "${{ inputs.installer-version }}"
    )
    assert install_env["WHITAKER_INSTALLER_CACHE_HIT"] == (
        "${{ steps.cache-whitaker-installer.outputs.cache-hit }}"
    )
    install_script = typ.cast("str", install_step["run"])
    assert "title=Whitaker installer cache" in install_script
    assert "title=Whitaker installer::status=complete" in install_script
    assert 'CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"' in install_script


def test_installs_with_cargo_binstall_when_available(tmp_path: Path) -> None:
    """Verify that an available cargo-binstall installs the pinned installer.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory used for deterministic Cargo and installer stubs.

    Examples
    --------
    Run this installation-path check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k installs_with_cargo_binstall_when_available

    Returns
    -------
    None
        Passes when cargo-binstall receives the pinned version and the action
        reports successful completion.
    """
    result = _run_install_script(tmp_path, _InstallScenario(binstall_available=True))

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
        "binstall --version",
        "binstall --no-confirm --locked whitaker-installer@0.2.6",
    ]
    assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
        "suite installed\n"
    )
    assert "::notice title=Whitaker installer::path=cargo-binstall version=0.2.6" in (
        result.stdout
    )
    assert "::notice title=Whitaker installer::status=complete version=0.2.6" in (
        result.stdout
    )


def test_falls_back_to_cargo_install(tmp_path: Path) -> None:
    """Verify the Cargo-install fallback when cargo-binstall is unavailable.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory used for deterministic Cargo and installer stubs.

    Examples
    --------
    Run this fallback-path check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k falls_back_to_cargo_install

    Returns
    -------
    None
        Passes when the action invokes ``cargo install`` with the pinned
        version and reports the fallback path.
    """
    result = _run_install_script(tmp_path, _InstallScenario(binstall_available=False))

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
        "binstall --version",
        "install --locked whitaker-installer --version 0.2.6",
    ]
    assert "cargo-binstall unavailable" in result.stdout
    assert "::notice title=Whitaker installer::path=cargo-install version=0.2.6" in (
        result.stdout
    )


def test_reuses_cached_installer(tmp_path: Path) -> None:
    """Verify that a restored installer bypasses both Cargo installation paths.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory used for deterministic Cargo and installer stubs.

    Examples
    --------
    Run this cache-path check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k reuses_cached_installer

    Returns
    -------
    None
        Passes when no Cargo command runs, the restored installer completes,
        and the action reports the cache path.
    """
    result = _run_install_script(
        tmp_path,
        _InstallScenario(
            binstall_available=False,
            installer_present=True,
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


def test_installs_nondefault_version_into_nondefault_cargo_home(
    tmp_path: Path,
) -> None:
    """A custom Cargo home should contain the requested installer version."""
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


def test_expands_tilde_cargo_home_before_prepending_path(tmp_path: Path) -> None:
    """The Cargo-home installer should override an ambient PATH installer."""
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


@_PROPERTY_TEST_SETTINGS
@given(installer_version=_VALID_INSTALLER_VERSIONS)
def test_installs_generated_valid_installer_versions(
    tmp_path_factory: pytest.TempPathFactory,
    installer_version: str,
) -> None:
    """Verify cargo-binstall accepts every generated supported version.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Factory that creates a fresh directory for each generated example.
    installer_version : str
        Generated non-empty dot-separated numeric installer version.

    Examples
    --------
    Run the property check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k generated_valid_installer_versions

    Returns
    -------
    None
        Passes when the generated version reaches cargo-binstall and the
        completion notice unchanged.
    """
    example_path = tmp_path_factory.mktemp("installer-version-")
    result = _run_install_script(
        example_path,
        _InstallScenario(
            binstall_available=True,
            installer_version=installer_version,
        ),
    )

    assert result.returncode == 0, result.stderr
    assert (example_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
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
    tmp_path_factory: pytest.TempPathFactory,
    cargo_home_form: str,
    segment: str,
) -> None:
    """Verify supported Cargo-home forms select the cached installer first.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Factory that creates a fresh directory for each generated example.
    cargo_home_form : str
        Absolute or tilde-relative Cargo-home representation under test.
    segment : str
        Generated safe path segment appended to the Cargo home.

    Examples
    --------
    Run the property check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k supported_cargo_home_forms

    Returns
    -------
    None
        Passes when the cached Cargo-home installer takes precedence over an
        ambient PATH installer without invoking Cargo.
    """
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
    tmp_path: Path,
    scenario: _InstallScenario,
) -> None:
    """Exhaustively verify the finite installer-state failure contract.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory used for deterministic Cargo and installer stubs.
    scenario : _InstallScenario
        One of the 32 combinations of cache availability, installer choice,
        and failure states supplied by the parametrization.

    Examples
    --------
    Run every bounded-state case directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k install_scenario_matrix

    Returns
    -------
    None
        Passes when each state returns success or failure according to the
        cache, selected Cargo path, and installer failure invariants.
    """
    result = _run_install_script(tmp_path, scenario)
    if scenario.installer_present:
        expected_failure = scenario.fail_installer
    elif scenario.binstall_available:
        expected_failure = scenario.fail_binstall or scenario.fail_installer
    else:
        expected_failure = scenario.fail_install or scenario.fail_installer

    assert (result.returncode != 0) is expected_failure, result.stderr


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
    tmp_path: Path,
    scenario: _InstallScenario,
    expected_error: str,
) -> None:
    """Verify actionable errors for each selected installer failure path.

    Parameters
    ----------
    tmp_path : Path
        Per-test directory used for deterministic Cargo and installer stubs.
    scenario : _InstallScenario
        Parametrized state that fails cargo-binstall, Cargo install, or the
        Whitaker installer after installation.
    expected_error : str
        Diagnostic fragment that the selected failure path must write to
        standard error.

    Examples
    --------
    Run every explicit failure-path check directly with:

    pytest .github/actions/install-whitaker/tests/test_install_whitaker.py \
        -k reports_install_failure

    Returns
    -------
    None
        Passes when the action exits non-zero and emits both the path-specific
        diagnostic and the structured failure annotation.
    """
    result = _run_install_script(tmp_path, scenario)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert (
        f"::error title=Whitaker installer failed::exit-code={result.returncode} "
        "version=0.2.6"
    ) in result.stderr
