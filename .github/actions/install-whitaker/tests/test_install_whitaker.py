"""Verify the install-whitaker action's contracts and installation paths.

The suite drives the composite action's Bash fragments with deterministic
release-download stubs, so no network access or real archive is required. It
covers checksum-verified installer acquisition against the action's pinned
digest manifest and the optional ``installer-sha256`` trust anchor, the
built-in and caller-owned cache providers, and the action manifest and
state-dependent behaviour of the install and run steps. Run it with ``uv run
pytest .github/actions/install-whitaker/tests/test_install_whitaker.py``.
"""

from __future__ import annotations

import os
import shutil
import string
import subprocess
import typing as typ
from dataclasses import dataclass  # noqa: ICN003 - required scenario decorator.
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"
DIGEST_MANIFEST_PATH = ACTION_DIR / "installer-digests.sha256"
_PAYLOAD_SHA256 = "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
_WRONG_SHA256 = "0" * 64
_PINNED_TARGETS = (
    "aarch64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
)
_PINNED_VERSIONS = ("0.2.6", "0.2.7")
_PROPERTY_TEST_SETTINGS = settings(
    deadline=None,
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
    """Return the installation lifecycle's shell fragments."""
    manifest = _load_manifest()
    runs = manifest["runs"]
    assert isinstance(runs, dict)
    steps = typ.cast("list[dict[str, object]]", runs["steps"])
    lifecycle_steps = {
        "Report Whitaker installer cache",
        "Install Whitaker installer",
        "Run Whitaker installer",
    }
    scripts = [
        step["run"]
        for step in steps
        if step.get("name") in lifecycle_steps and isinstance(step.get("run"), str)
    ]
    assert len(scripts) == len(lifecycle_steps)
    return "\n".join(typ.cast("list[str]", scripts))


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


def _write_release_stubs(bin_dir: Path) -> None:
    """Write release-download stubs that install a deterministic binary."""
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOWNLOAD_LOG"
if [ "$FAIL_DOWNLOAD" = "true" ]; then
  echo "Whitaker release download failed" >&2
  exit 31
fi
output=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ "$url" == *.sha256 ]]; then
  printf '%s  archive\n' "$SIDECAR_SHA256" > "$output"
else
  printf '%s' "$ARCHIVE_PAYLOAD" > "$output"
fi
""",
    )
    _write_executable(
        bin_dir / "tar",
        """#!/usr/bin/env bash
set -euo pipefail
extract_dir=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -C) extract_dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat > "$extract_dir/whitaker-installer" <<'INSTALLER'
#!/usr/bin/env bash
set -euo pipefail
if [ "$FAIL_INSTALLER" = "true" ]; then
  echo "whitaker-installer failed while installing the Dylint suite" >&2
  exit 33
fi
printf '%s\n' "suite installed" >> "$INSTALLER_LOG"
INSTALLER
chmod +x "$extract_dir/whitaker-installer"
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
    installer_present: bool = False
    fail_download: bool = False
    fail_installer: bool = False
    installer_version: str = "0.2.7"
    cargo_home_name: str = "cargo-home"
    cargo_home_value: str | None = None
    cache_hit: bool = False
    cache_provider: str = "github"
    conflicting_installer: bool = False
    archive_payload: str = "payload"
    sidecar_sha256: str = _PAYLOAD_SHA256
    pinned_sha256: str | None = _PAYLOAD_SHA256
    installer_sha256: str = ""

    @property
    def asset(self) -> str:
        """Return the Linux x64 release asset the fragment resolves."""
        return (
            f"whitaker-installer-x86_64-unknown-linux-gnu-v{self.installer_version}.tgz"
        )


@dataclass(frozen=True)
class _InstallPaths:
    """Contain fixture paths in Bash-compatible form."""

    bin_dir: Path
    bash_cargo_home: str
    bash_bin_dir: str
    bash_download_log: str
    bash_conflict_log: str
    bash_home_dir: str
    bash_installer_log: str
    bash_summary_log: str
    bash_digest_manifest: str


@dataclass(frozen=True)
class _InputValidationCase:
    """Describe one invalid action-input contract case."""

    cargo_home: str
    installer_version: str
    expected_error: str


@dataclass(frozen=True)
class _ValidationInputs:
    """Describe the action inputs supplied to the validation fragment."""

    cargo_home: str
    installer_version: str
    cache_provider: str = "github"
    runner_os: str = "Linux"
    installer_sha256: str = ""


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
    inputs: _ValidationInputs,
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
            "CACHE_PROVIDER_INPUT": inputs.cache_provider,
            "CARGO_HOME_INPUT": inputs.cargo_home,
            "GITHUB_OUTPUT": (
                f"{_bash_path(bash, output_path.parent)}/{output_path.name}"
            ),
            "HOME": _bash_path(bash, home_dir),
            "INSTALLER_SHA256_INPUT": inputs.installer_sha256,
            "INSTALLER_VERSION_INPUT": inputs.installer_version,
            "RUNNER_OS": inputs.runner_os,
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

    paths = _create_install_paths(tmp_path, scenario, bash)
    original_path = _prepare_install_stubs(tmp_path, scenario, paths, bash)
    env = _build_install_environment(scenario, paths, original_path)
    return _execute_install_script(bash, tmp_path, env)


def _create_install_paths(
    tmp_path: Path,
    scenario: _InstallScenario,
    bash: str,
) -> _InstallPaths:
    """Create deterministic paths for an installation scenario."""
    cargo_home = tmp_path / scenario.cargo_home_name
    bin_dir = cargo_home / "bin"
    bin_dir.mkdir(parents=True)
    download_log = tmp_path / "download.log"
    installer_log = tmp_path / "installer.log"
    conflict_log = tmp_path / "conflict.log"
    summary_log = tmp_path / "summary.md"
    digest_manifest = tmp_path / "installer-digests.sha256"
    if scenario.pinned_sha256 is not None:
        digest_manifest.write_text(
            f"{scenario.pinned_sha256}  {scenario.asset}\n",
            encoding="utf-8",
        )
    else:
        digest_manifest.write_text("# no pinned digests\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir(exist_ok=True)
    return _InstallPaths(
        bin_dir=bin_dir,
        bash_cargo_home=_bash_path(bash, cargo_home),
        bash_bin_dir=_bash_path(bash, bin_dir),
        bash_download_log=(
            f"{_bash_path(bash, download_log.parent)}/{download_log.name}"
        ),
        bash_conflict_log=(
            f"{_bash_path(bash, conflict_log.parent)}/{conflict_log.name}"
        ),
        bash_home_dir=_bash_path(bash, home_dir),
        bash_installer_log=(
            f"{_bash_path(bash, installer_log.parent)}/{installer_log.name}"
        ),
        bash_summary_log=(f"{_bash_path(bash, summary_log.parent)}/{summary_log.name}"),
        bash_digest_manifest=(
            f"{_bash_path(bash, digest_manifest.parent)}/{digest_manifest.name}"
        ),
    )


def _prepare_install_stubs(
    tmp_path: Path,
    scenario: _InstallScenario,
    paths: _InstallPaths,
    bash: str,
) -> str:
    """Create release and installer stubs and return the ambient PATH."""
    _write_release_stubs(paths.bin_dir)
    original_path = f"{paths.bash_bin_dir}:/usr/bin:/bin"
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
            paths.bin_dir / "whitaker-installer",
            """#!/usr/bin/env bash
set -euo pipefail
if [ "$FAIL_INSTALLER" = "true" ]; then
  echo "whitaker-installer failed while installing the Dylint suite" >&2
  exit 33
fi
printf '%s\n' "suite installed" >> "$INSTALLER_LOG"
""",
        )
    return original_path


def _build_install_environment(
    scenario: _InstallScenario,
    paths: _InstallPaths,
    original_path: str,
) -> dict[str, str]:
    """Build the installation fragment environment."""
    return {
        **os.environ,
        "PATH": original_path,
        "BASH_ENV": "",
        "CARGO_HOME": scenario.cargo_home_value or paths.bash_cargo_home,
        "HOME": paths.bash_home_dir,
        "ARCHIVE_PAYLOAD": scenario.archive_payload,
        "DOWNLOAD_LOG": paths.bash_download_log,
        "CONFLICT_LOG": paths.bash_conflict_log,
        "SIDECAR_SHA256": scenario.sidecar_sha256,
        "FAIL_DOWNLOAD": str(scenario.fail_download).lower(),
        "FAIL_INSTALLER": str(scenario.fail_installer).lower(),
        "INSTALLER_LOG": paths.bash_installer_log,
        "GITHUB_STEP_SUMMARY": paths.bash_summary_log,
        "RUNNER_ARCHITECTURE": "X64",
        "RUNNER_OPERATING_SYSTEM": "Linux",
        "WHITAKER_CACHE_PROVIDER": scenario.cache_provider,
        "WHITAKER_INSTALLER_CACHE_HIT": str(scenario.cache_hit).lower(),
        "WHITAKER_DIGEST_MANIFEST": paths.bash_digest_manifest,
        "WHITAKER_INSTALLER_PATH": f"{paths.bash_bin_dir}/whitaker-installer",
        "WHITAKER_INSTALLER_SHA256": scenario.installer_sha256,
        "WHITAKER_INSTALLER_VERSION": scenario.installer_version,
    }


def _assert_manifest_inputs(manifest: dict[str, object]) -> None:
    """Assert the action input contract."""
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
            "default": "0.2.7",
        },
        "installer-sha256": {
            "description": (
                "SHA-256 digest of the whitaker-installer release archive for "
                "this runner. Required only for a version absent from the "
                "action's pinned digest manifest; the pinned digest is used "
                "when this is empty."
            ),
            "required": False,
            "default": "",
        },
        "cache-provider": {
            "description": (
                'Cache owner for the installer binary. Use "github" for the '
                'action\'s built-in cache or "external" when the caller mounts '
                "the Cargo home."
            ),
            "required": False,
            "default": "github",
        },
    }


def _assert_validate_step(validate_step: dict[str, object]) -> None:
    """Assert the input-validation step contract."""
    assert validate_step["id"] == "validate-inputs"
    validate_env = typ.cast("dict[str, str]", validate_step["env"])
    assert validate_env == {
        "CACHE_PROVIDER_INPUT": "${{ inputs.cache-provider }}",
        "CARGO_HOME_INPUT": "${{ inputs.cargo-home }}",
        "INSTALLER_SHA256_INPUT": "${{ inputs.installer-sha256 }}",
        "INSTALLER_VERSION_INPUT": "${{ inputs.installer-version }}",
    }
    validate_script = typ.cast("str", validate_step["run"])
    assert "must not contain a carriage return or newline" in validate_script
    assert "must be an absolute path or start with ~/" in validate_script
    assert "must not contain the runner PATH separator" in validate_script
    assert "without leading zeros" in validate_script
    assert "cache-provider must be github or external" in validate_script
    assert "installer-sha256 must be 64 hexadecimal characters" in validate_script


def _assert_cache_step(cache_step: dict[str, object]) -> None:
    """Assert the installer-cache step contract."""
    assert cache_step["id"] == "cache-whitaker-installer"
    assert cache_step["uses"] == (
        "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    )
    assert cache_step["if"] == "${{ inputs.cache-provider == 'github' }}"
    cache_config = typ.cast("dict[str, str]", cache_step["with"])
    assert cache_config["path"].splitlines() == [
        "${{ steps.validate-inputs.outputs.installer-path }}",
        "~/.local/share/whitaker",
    ]
    assert cache_config["key"] == (
        "whitaker-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.validate-inputs.outputs.installer-version }}-"
        "${{ hashFiles('dylint.toml') }}-"
        "${{ steps.validate-inputs.outputs.cargo-home }}"
    )


def _assert_cache_report_step(cache_report_step: dict[str, object]) -> None:
    """Assert the cache-reporting step contract."""
    cache_report_env = typ.cast("dict[str, str]", cache_report_step["env"])
    assert cache_report_env["WHITAKER_CACHE_PROVIDER"] == (
        "${{ inputs.cache-provider }}"
    )
    assert cache_report_env["WHITAKER_INSTALLER_CACHE_HIT"] == (
        "${{ steps.cache-whitaker-installer.outputs.cache-hit }}"
    )
    cache_report_script = typ.cast("str", cache_report_step["run"])
    assert "provider=${WHITAKER_CACHE_PROVIDER}" in cache_report_script


def _assert_install_step(install_step: dict[str, object]) -> None:
    """Assert the prebuilt release installation step contract."""
    install_env = typ.cast("dict[str, str]", install_step["env"])
    assert install_env["WHITAKER_DIGEST_MANIFEST"] == (
        "${{ github.action_path }}/installer-digests.sha256"
    )
    assert install_env["WHITAKER_INSTALLER_SHA256"] == (
        "${{ steps.validate-inputs.outputs.installer-sha256 }}"
    )
    assert install_env["RUNNER_ARCHITECTURE"] == "${{ runner.arch }}"
    assert install_env["RUNNER_OPERATING_SYSTEM"] == "${{ runner.os }}"
    assert install_env["WHITAKER_INSTALLER_PATH"] == (
        "${{ steps.validate-inputs.outputs.installer-path }}"
    )
    assert install_env["WHITAKER_INSTALLER_VERSION"] == (
        "${{ steps.validate-inputs.outputs.installer-version }}"
    )
    install_script = typ.cast("str", install_step["run"])
    assert "releases/download/v${WHITAKER_INSTALLER_VERSION}" in install_script
    assert "curl -fsSL --proto '=https' --tlsv1.2" in install_script
    assert "sha256sum" in install_script
    assert "cargo install" not in install_script
    assert "cargo binstall" not in install_script
    assert "WHITAKER_DIGEST_MANIFEST" in install_script
    assert "whitaker-installer.trust-anchor=" in install_script


def _assert_run_step(run_step: dict[str, object]) -> None:
    """Assert the installer execution step contract."""
    run_env = typ.cast("dict[str, str]", run_step["env"])
    assert run_env["WHITAKER_INSTALLER_PATH"] == (
        "${{ steps.validate-inputs.outputs.installer-path }}"
    )
    run_script = typ.cast("str", run_step["run"])
    assert '"$WHITAKER_INSTALLER_PATH"' in run_script
    assert "title=Whitaker installer::status=complete" in run_script


class TestManifest:
    """Validate the action manifest's declared contract."""

    def test_manifest_exposes_version_and_cache_contract(self) -> None:
        """Verify the manifest's versioned installer-cache contract."""
        manifest = _load_manifest()

        _assert_manifest_inputs(manifest)
        runs = manifest["runs"]
        assert isinstance(runs, dict)
        steps = typ.cast("list[dict[str, object]]", runs["steps"])
        validate_step, cache_step, cache_report_step, install_step, run_step = steps
        _assert_validate_step(validate_step)
        _assert_cache_step(cache_step)
        _assert_cache_report_step(cache_report_step)
        _assert_install_step(install_step)
        _assert_run_step(run_step)

    def test_normalizes_valid_action_inputs(self, tmp_path: Path) -> None:
        """Verify validation expands the supported tilde Cargo-home form."""
        result = _run_input_validation(tmp_path, _ValidationInputs("~/.cargo", "1.2.3"))

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "output").read_text(encoding="utf-8").splitlines() == [
            f"cargo-home={
                _bash_path(shutil.which('bash') or 'bash', tmp_path / 'home')
            }/.cargo",
            f"installer-path={
                _bash_path(shutil.which('bash') or 'bash', tmp_path / 'home')
            }/.cargo/bin/whitaker-installer",
            "installer-version=1.2.3",
            "installer-sha256=",
        ]

    def test_rejects_unknown_cache_provider(self, tmp_path: Path) -> None:
        """Verify cache ownership fails closed before cache evaluation."""
        result = _run_input_validation(
            tmp_path,
            _ValidationInputs("~/.cargo", "1.2.3", cache_provider="namespace"),
        )

        assert result.returncode != 0
        assert "cache-provider must be github or external" in result.stderr

    def test_selects_windows_executable_suffix(self, tmp_path: Path) -> None:
        """Verify Windows caches and executes the native installer filename."""
        result = _run_input_validation(
            tmp_path,
            _ValidationInputs("~/.cargo", "1.2.3", runner_os="Windows"),
        )

        assert result.returncode == 0, result.stderr
        output = (tmp_path / "output").read_text(encoding="utf-8")
        assert "installer-path=" in output
        assert (
            output.split("installer-path=", maxsplit=1)[1]
            .splitlines()[0]
            .endswith("/whitaker-installer.exe")
        )

    @pytest.mark.parametrize(
        ("case", "runner_os"),
        [
            pytest.param(
                _InputValidationCase(
                    "~/.cargo\ninjected-path",
                    "1.2.3",
                    "cargo-home must not contain a carriage return or newline",
                ),
                "Linux",
                id="cargo-home-newline",
            ),
            pytest.param(
                _InputValidationCase(
                    "~/.cargo",
                    "1.2.3\ninjected-command",
                    "installer-version must not contain a carriage return or newline",
                ),
                "Linux",
                id="installer-version-newline",
            ),
            pytest.param(
                _InputValidationCase(
                    "relative/.cargo",
                    "1.2.3",
                    "cargo-home must be an absolute path or start with ~/",
                ),
                "Linux",
                id="relative-cargo-home",
            ),
            pytest.param(
                _InputValidationCase(
                    "/cargo-home:unsafe",
                    "1.2.3",
                    "cargo-home must not contain the runner PATH separator",
                ),
                "Linux",
                id="linux-cargo-home-path-separator",
            ),
            pytest.param(
                _InputValidationCase(
                    "~/.cargo;unsafe",
                    "1.2.3",
                    "cargo-home must not contain the runner PATH separator",
                ),
                "Windows",
                id="windows-cargo-home-path-separator",
            ),
            pytest.param(
                _InputValidationCase(
                    "~/.cargo",
                    "01.2.3",
                    "installer-version must be one to three numeric components "
                    "without leading zeros",
                ),
                "Linux",
                id="leading-zero-version",
            ),
            pytest.param(
                _InputValidationCase(
                    "~/.cargo",
                    "1" * 129,
                    "installer-version must be at most 128 characters",
                ),
                "Linux",
                id="overlong-version",
            ),
        ],
    )
    def test_rejects_unsafe_action_inputs(
        self,
        tmp_path: Path,
        case: _InputValidationCase,
        runner_os: str,
    ) -> None:
        """Verify malformed action inputs fail before cache evaluation."""
        result = _run_input_validation(
            tmp_path,
            _ValidationInputs(
                case.cargo_home,
                case.installer_version,
                runner_os=runner_os,
            ),
        )

        assert result.returncode != 0
        assert case.expected_error in result.stderr


class TestInstallation:
    """Exercise installation, cache, and PATH precedence paths."""

    def test_installs_official_release_binary(self, tmp_path: Path) -> None:
        """Verify the pinned official release supplies the installer."""
        result = _run_install_script(tmp_path, _InstallScenario())

        assert result.returncode == 0, result.stderr
        download_log = (tmp_path / "download.log").read_text(encoding="utf-8")
        asset = "whitaker-installer-x86_64-unknown-linux-gnu-v0.2.7.tgz"
        assert f"/v0.2.7/{asset} " in download_log
        assert f"/v0.2.7/{asset}.sha256 " in download_log
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert (
            "::notice title=Whitaker installer::path=official-release version=0.2.7"
            in result.stdout
        )
        assert (
            "::notice title=Whitaker installer::status=complete version=0.2.7"
            in result.stdout
        )
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.digest=verified",
            "whitaker-installer.trust-anchor=pinned",
            "whitaker-installer.path=official-release",
            "whitaker-installer.result=success",
        ]

    def test_reuses_cached_installer(self, tmp_path: Path) -> None:
        """Verify a restored installer bypasses the release download."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                installer_present=True,
                cache_hit=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert not (tmp_path / "download.log").exists()
        assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
            "suite installed\n"
        )
        assert "::notice title=Whitaker installer::path=cache version=0.2.7" in (
            result.stdout
        )
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=hit",
            "whitaker-installer.path=cache",
            "whitaker-installer.result=success",
        ]

    def test_reports_external_cache_ownership(self, tmp_path: Path) -> None:
        """Verify caller-owned cache mode reports the built-in cache disabled."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(installer_present=True, cache_provider="external"),
        )

        assert result.returncode == 0, result.stderr
        assert "provider=external state=disabled" in result.stdout
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines()[
            0
        ] == "whitaker-installer.cache=disabled"

    def test_installs_nondefault_version_into_nondefault_cargo_home(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a custom Cargo home contains the requested installer version."""
        scenario = _InstallScenario(
            installer_version="9.9.9",
            cargo_home_name="custom-cargo-home",
        )
        result = _run_install_script(tmp_path, scenario)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "custom-cargo-home" / "bin" / "whitaker-installer").is_file()
        assert "/v9.9.9/whitaker-installer-x86_64-unknown-linux-gnu-v9.9.9.tgz" in (
            tmp_path / "download.log"
        ).read_text(encoding="utf-8")
        assert "state=miss version=9.9.9" in result.stdout

    def test_expands_tilde_cargo_home_before_prepending_path(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the Cargo-home installer overrides an ambient PATH installer."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
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
        assert not (tmp_path / "download.log").exists()
        assert not (tmp_path / "conflict.log").exists()

    def test_installs_into_expanded_tilde_cargo_home_before_ambient_path(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify expanded Cargo home wins over an ambient PATH installer."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                cargo_home_name="home/.cargo",
                cargo_home_value="~/.cargo",
                conflicting_installer=True,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "home" / ".cargo" / "bin" / "whitaker-installer").is_file()
        assert (tmp_path / "download.log").is_file()
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
        """Verify release URLs accept each generated compatible version."""
        example_path = tmp_path_factory.mktemp("installer-version-")
        result = _run_install_script(
            example_path,
            _InstallScenario(
                installer_version=installer_version,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert f"/v{installer_version}/whitaker-installer-" in (
            example_path / "download.log"
        ).read_text(encoding="utf-8")
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
        assert not (example_path / "download.log").exists()


def _scenario_should_fail(scenario: _InstallScenario) -> bool:
    """Return whether the selected installer path is expected to fail."""
    if scenario.installer_present:
        return scenario.fail_installer
    return scenario.fail_download or scenario.fail_installer


class TestScenarioMatrix:
    """Check every bounded installer-state combination."""

    @pytest.mark.parametrize(
        "scenario",
        [
            _InstallScenario(
                installer_present=installer_present,
                fail_download=fail_download,
                fail_installer=fail_installer,
            )
            for installer_present in (False, True)
            for fail_download in (False, True)
            for fail_installer in (False, True)
        ],
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
                _InstallScenario(fail_download=True),
                "Whitaker release download failed",
                id="release-download",
            ),
            pytest.param(
                _InstallScenario(fail_installer=True),
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
            "version=0.2.7"
        ) in result.stderr
        assert "whitaker-installer.failure=" in (tmp_path / "summary.md").read_text(
            encoding="utf-8"
        )


class TestPinnedDigestManifest:
    """Validate the checked-in trust anchor for installer archives."""

    def test_manifest_pins_every_supported_target(self) -> None:
        """Verify each supported version and target has a pinned digest."""
        entries = {
            asset: digest
            for digest, asset in (
                line.split()
                for line in DIGEST_MANIFEST_PATH.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line and not line.startswith("#")
            )
        }

        expected = {
            (
                f"whitaker-installer-{target}-v{version}."
                f"{'zip' if target.endswith('windows-msvc') else 'tgz'}"
            )
            for version in _PINNED_VERSIONS
            for target in _PINNED_TARGETS
        }
        assert entries.keys() == expected
        assert all(
            len(digest) == 64 and set(digest) <= set(string.hexdigits.lower())
            for digest in entries.values()
        )


class TestTrustAnchor:
    """Check that installation depends on an independent pinned digest."""

    def test_rejects_mismatched_archive_digest(self, tmp_path: Path) -> None:
        """Verify a tampered archive leaves no installer and fails loudly."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(pinned_sha256=_WRONG_SHA256),
        )

        assert result.returncode != 0
        assert "archive digest mismatch" in result.stderr
        assert not (tmp_path / "cargo-home" / "bin" / "whitaker-installer").exists()
        assert (tmp_path / "summary.md").read_text(encoding="utf-8").splitlines() == [
            "whitaker-installer.cache=miss",
            "whitaker-installer.digest=mismatch",
            "whitaker-installer.failure=install",
        ]
        assert not (tmp_path / "installer.log").exists()

    def test_rejects_release_sidecar_disagreement(self, tmp_path: Path) -> None:
        """Verify the sidecar must agree with the verified archive digest."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(sidecar_sha256=_WRONG_SHA256),
        )

        assert result.returncode != 0
        assert "disagrees with the verified archive digest" in result.stderr
        assert not (tmp_path / "cargo-home" / "bin" / "whitaker-installer").exists()
        assert "whitaker-installer.digest=sidecar-mismatch" in (
            tmp_path / "summary.md"
        ).read_text(encoding="utf-8")

    def test_unknown_version_without_digest_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an unpinned version refuses to download or install."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(installer_version="9.9.9", pinned_sha256=None),
        )

        assert result.returncode != 0
        assert "no pinned SHA-256 for" in result.stderr
        assert not (tmp_path / "download.log").exists()
        assert not (tmp_path / "cargo-home" / "bin" / "whitaker-installer").exists()
        assert "whitaker-installer.digest=unpinned" in (
            tmp_path / "summary.md"
        ).read_text(encoding="utf-8")

    def test_caller_supplied_digest_installs_unpinned_version(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify ``installer-sha256`` anchors a version absent from the table."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                installer_version="9.9.9",
                pinned_sha256=None,
                installer_sha256=_PAYLOAD_SHA256,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "cargo-home" / "bin" / "whitaker-installer").is_file()
        assert "whitaker-installer.trust-anchor=input" in (
            tmp_path / "summary.md"
        ).read_text(encoding="utf-8")

    def test_caller_supplied_digest_still_verifies_the_archive(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify a caller digest is enforced, not merely recorded."""
        result = _run_install_script(
            tmp_path,
            _InstallScenario(
                installer_version="9.9.9",
                pinned_sha256=None,
                installer_sha256=_WRONG_SHA256,
            ),
        )

        assert result.returncode != 0
        assert "archive digest mismatch" in result.stderr
        assert not (tmp_path / "cargo-home" / "bin" / "whitaker-installer").exists()

    @pytest.mark.parametrize(
        "installer_sha256",
        ["not-a-digest", _PAYLOAD_SHA256[:-1], f"{_PAYLOAD_SHA256}0"],
    )
    def test_rejects_malformed_installer_digest_input(
        self,
        tmp_path: Path,
        installer_sha256: str,
    ) -> None:
        """Verify a malformed digest input fails before any download."""
        result = _run_input_validation(
            tmp_path,
            _ValidationInputs(
                "~/.cargo",
                "1.2.3",
                installer_sha256=installer_sha256,
            ),
        )

        assert result.returncode != 0
        assert "installer-sha256 must be 64 hexadecimal characters" in result.stderr

    def test_normalizes_uppercase_installer_digest_input(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify an uppercase digest input is lowercased for comparison."""
        result = _run_input_validation(
            tmp_path,
            _ValidationInputs(
                "~/.cargo",
                "1.2.3",
                installer_sha256=_PAYLOAD_SHA256.upper(),
            ),
        )

        assert result.returncode == 0, result.stderr
        assert f"installer-sha256={_PAYLOAD_SHA256}" in (tmp_path / "output").read_text(
            encoding="utf-8"
        )
