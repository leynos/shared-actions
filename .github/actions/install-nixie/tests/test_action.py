"""Verify the version-aware, binary-only install-nixie action contract."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ
from dataclasses import dataclass  # noqa: ICN003 - concise scenario declarations.
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
PROBE_STEP_ID = "check-cargo-binstall"
INSTALL_STEP_ID = "install-nixie-merman"
BINSTALL_ACTION_PIN = "8f137732cada3c067cb574fe3537fedfe2a6451d"
BINSTALL_VERSION = "1.22.0"
SUPPORTED_TARGETS = {
    ("Linux", "X64"): "x86_64-unknown-linux-gnu",
    ("macOS", "ARM64"): "aarch64-apple-darwin",
    ("macOS", "X64"): "x86_64-apple-darwin",
    ("Windows", "X64"): "x86_64-pc-windows-msvc",
}


@dataclass(frozen=True)
class _ProbeOptions:
    """Describe one cargo-binstall availability probe scenario."""

    cargo_present: bool
    binstall_present: bool
    binstall_exit_status: int


@dataclass(frozen=True)
class _InstallOptions:
    """Describe one deterministic installation scenario."""

    merman_cached: bool = True
    cached_merman_version: str | None = None
    cached_merman_exit_status: int = 0
    cached_merman_executable: bool = True
    merman_version: str = "0.7.0"
    installed_merman_version: str | None = None
    installed_merman_exit_status: int = 0
    install_creates_merman: bool = True
    install_merman_executable: bool = True
    merman_install_status: int = 0
    nixie_shim_after_install: bool = True
    nixie_shim_after_force: bool = True
    nixie_version: str = "1.1.0"
    python_version: str = "3.14"
    include_uv: bool = True
    include_cargo: bool = True
    runner_arch: str = "X64"
    runner_os: str = "Linux"
    uv_bin_dir_output: str | None = None
    uv_install_status: int = 0


def _load_action() -> dict[str, typ.Any]:
    """Load the install-nixie action manifest."""
    return typ.cast(
        "dict[str, typ.Any]", yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    )


def _step_with_id(step_id: str) -> dict[str, typ.Any]:
    """Find exactly one composite-action step by its stable ID."""
    steps = _load_action()["runs"]["steps"]
    matches = [step for step in steps if step.get("id") == step_id]
    assert len(matches) == 1, (
        f"expected one step with ID {step_id!r}, found {len(matches)}"
    )
    return matches[0]


def _probe_script() -> str:
    """Return the cargo-binstall usability probe shell fragment."""
    script = _step_with_id(PROBE_STEP_ID).get("run")
    assert isinstance(script, str), "cargo-binstall probe must define a shell script"
    return script


def _install_script() -> str:
    """Return the final CLI installation shell fragment."""
    script = _step_with_id(INSTALL_STEP_ID).get("run")
    assert isinstance(script, str), "install step must define a shell script"
    return script


def _write_executable(path: Path, content: str, *, executable: bool = True) -> None:
    """Write a deterministic shell command stub."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)


def _merman_stub(version: str, exit_status: int) -> str:
    """Return a Merman command stub that reports a fixed version."""
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--version" ]]; then\n'
        f"  printf '%s\\n' 'merman-cli {version}'\n"
        f"  exit {exit_status}\n"
        "fi\n"
        "exit 0\n"
    )


def _write_cargo_stubs(stubs_dir: Path) -> None:
    """Write deterministic Cargo and cargo-binstall command stubs."""
    _write_executable(
        stubs_dir / "cargo",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "binstall" ]]; then
  exit 64
fi
shift
exec cargo-binstall "$@"
""",
    )
    _write_executable(
        stubs_dir / "cargo-binstall",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'cargo binstall <%s>\\n' "$*" >> "$CALLS_PATH"
if [[ "$BINSTALL_INSTALL_STATUS" != "0" ]]; then
  exit "$BINSTALL_INSTALL_STATUS"
fi
install_path=""
while (($#)); do
  if [[ "$1" == "--install-path" ]]; then
    install_path="$2"
    shift 2
  else
    shift
  fi
done
if [[ "$BINSTALL_CREATES_MERMAN" == "true" ]]; then
  mkdir -p -- "$install_path"
  printf '%s' "$BINSTALL_MERMAN_SCRIPT" > "$install_path/$MERMAN_EXECUTABLE"
  if [[ "$BINSTALL_MERMAN_EXECUTABLE" == "true" ]]; then
    chmod +x "$install_path/$MERMAN_EXECUTABLE"
  fi
fi
""",
    )


def _write_cygpath_stub(stubs_dir: Path) -> None:
    """Write the Windows path-conversion stub used by the final step."""
    _write_executable(
        stubs_dir / "cygpath",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'cygpath <%s>\\n' "$*" >> "$CALLS_PATH"
case "${1:-}" in
  --unix) printf '%s\\n' "$UV_BIN_DIR" ;;
  --windows) printf '%s\\n' "$MERMAN_WINDOWS_BIN_DIR" ;;
  *) exit 64 ;;
esac
""",
    )


def _write_uv_stub(stubs_dir: Path) -> None:
    """Write a uv stub that models normal and forced shim reconciliation."""
    _write_executable(
        stubs_dir / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv <%s>\\n' "$*" >> "$CALLS_PATH"
if [[ "${1:-}" == "tool" && "${2:-}" == "dir" && "${3:-}" == "--bin" ]]; then
  printf '%s\\n' "$UV_BIN_DIR_OUTPUT"
  exit 0
fi
if [[ "${1:-}" == "tool" && "${2:-}" == "install" ]]; then
  force=false
  for argument in "$@"; do
    if [[ "$argument" == "--force" ]]; then
      force=true
    fi
  done
  if [[ "$UV_INSTALL_STATUS" != "0" ]]; then
    exit "$UV_INSTALL_STATUS"
  fi
  should_create="$NIXIE_SHIM_AFTER_INSTALL"
  if [[ "$force" == true ]]; then
    should_create="$NIXIE_SHIM_AFTER_FORCE"
  fi
  if [[ "$should_create" == "true" ]]; then
    mkdir -p "$UV_BIN_DIR"
    printf '#!/usr/bin/env bash\\nexit 0\\n' > "$UV_BIN_DIR/$UV_EXECUTABLE"
    chmod +x "$UV_BIN_DIR/$UV_EXECUTABLE"
  fi
fi
""",
    )


def _isolated_path(stubs_dir: Path, bash: str) -> str:
    """Build a minimal PATH with shell utilities but no host Rust toolchain."""
    if os.name == "nt":
        git_root = Path(bash).parent.parent
        runtime_dirs = (git_root / "usr" / "bin", git_root / "bin")
    else:
        runtime_dirs = (Path("/usr/bin"), Path("/bin"))
    return os.pathsep.join(str(path) for path in (stubs_dir, *runtime_dirs))


def _run_probe(
    tmp_path: Path,
    *,
    cargo_present: bool,
    binstall_present: bool,
    binstall_exit_status: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Execute only the probe with an isolated PATH and Cargo stub set."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    stubs_dir = tmp_path / "probe-stubs"
    stubs_dir.mkdir()
    calls_path = tmp_path / "probe-calls"
    github_output = tmp_path / "github-output"
    bash_shebang = "#!/usr/bin/env bash\n"
    if cargo_present:
        _write_executable(
            stubs_dir / "cargo",
            bash_shebang
            + """set -euo pipefail
printf 'cargo <%s>\\n' "$*" >> "$CALLS_PATH"
if [[ "${1:-}" != "binstall" ]]; then
  exit 64
fi
if [[ "$BINSTALL_PRESENT" != "true" ]]; then
  exit 127
fi
shift
exec cargo-binstall "$@"
""",
        )
    if binstall_present:
        _write_executable(
            stubs_dir / "cargo-binstall",
            bash_shebang
            + """set -euo pipefail
printf 'cargo-binstall <%s>\\n' "$*" >> "$CALLS_PATH"
exit "$BINSTALL_PROBE_STATUS"
""",
        )

    env = {
        "BINSTALL_PROBE_STATUS": str(binstall_exit_status),
        "BINSTALL_PRESENT": str(binstall_present).lower(),
        "CALLS_PATH": calls_path.as_posix(),
        "GITHUB_OUTPUT": github_output.as_posix(),
        "PATH": (_isolated_path(stubs_dir, bash) if cargo_present else ""),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _probe_script()],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_install_script(
    tmp_path: Path,
    options: _InstallOptions,
) -> subprocess.CompletedProcess[str]:
    """Execute the installation fragment against deterministic command stubs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    env = _install_environment(tmp_path, options, bash)
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _install_script()],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _install_environment(
    tmp_path: Path, options: _InstallOptions, bash: str
) -> dict[str, str]:
    """Prepare deterministic CLI stubs, cache contents and action variables."""
    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    if options.include_cargo:
        _write_cargo_stubs(stubs_dir)
    _write_cygpath_stub(stubs_dir)
    if options.include_uv:
        _write_uv_stub(stubs_dir)

    calls_path = tmp_path / "calls"
    github_path = tmp_path / "github-path"
    cache_home = tmp_path / "cache-home"
    uv_bin_dir = tmp_path / "uv-bin"
    uv_bin_dir_output = options.uv_bin_dir_output or uv_bin_dir.as_posix()
    is_windows_runner = options.runner_os == "Windows"
    merman_executable = "merman-cli.exe" if is_windows_runner else "merman-cli"
    nixie_executable = "nixie.exe" if is_windows_runner else "nixie"
    merman_bin_dir = cache_home / "merman" / options.merman_version / "bin"
    merman_path = merman_bin_dir / merman_executable
    windows_merman_bin_dir = rf"C:\runner\cache\merman\{options.merman_version}\bin"
    if options.merman_cached:
        cached_version = options.cached_merman_version or options.merman_version
        merman_path.parent.mkdir(parents=True)
        _write_executable(
            merman_path,
            _merman_stub(cached_version, options.cached_merman_exit_status),
            executable=options.cached_merman_executable,
        )

    path = _isolated_path(stubs_dir, bash) if options.include_uv else str(stubs_dir)
    return {
        **os.environ,
        "BINSTALL_CREATES_MERMAN": str(options.install_creates_merman).lower(),
        "BINSTALL_INSTALL_STATUS": str(options.merman_install_status),
        "BINSTALL_MERMAN_EXECUTABLE": str(options.install_merman_executable).lower(),
        "BINSTALL_MERMAN_SCRIPT": _merman_stub(
            options.installed_merman_version or options.merman_version,
            options.installed_merman_exit_status,
        ),
        "CALLS_PATH": calls_path.as_posix(),
        "GITHUB_PATH": github_path.as_posix(),
        "MERMAN_EXECUTABLE": merman_executable,
        "MERMAN_VERSION": options.merman_version,
        "NIXIE_SHIM_AFTER_FORCE": str(options.nixie_shim_after_force).lower(),
        "NIXIE_SHIM_AFTER_INSTALL": str(options.nixie_shim_after_install).lower(),
        "NIXIE_VERSION": options.nixie_version,
        "PATH": path,
        "PYTHON_VERSION": options.python_version,
        "RUNNER_ARCH": options.runner_arch,
        "RUNNER_OS": options.runner_os,
        "UV_BIN_DIR": uv_bin_dir.as_posix(),
        "UV_BIN_DIR_OUTPUT": uv_bin_dir_output,
        "UV_EXECUTABLE": nixie_executable,
        "UV_INSTALL_STATUS": str(options.uv_install_status),
        "XDG_CACHE_HOME": cache_home.as_posix(),
        "MERMAN_WINDOWS_BIN_DIR": windows_merman_bin_dir,
    }


def _calls(tmp_path: Path, *, probe: bool = False) -> list[str]:
    """Return recorded external command calls."""
    calls_path = tmp_path / ("probe-calls" if probe else "calls")
    if not calls_path.exists():
        return []
    return calls_path.read_text(encoding="utf-8").splitlines()


def _assert_github_path_empty(tmp_path: Path) -> None:
    """Assert that the action did not export a tool directory."""
    github_path = tmp_path / "github-path"
    assert not github_path.exists() or github_path.read_text(encoding="utf-8") == ""


def _expected_binstall_call(bin_dir: Path, version: str, target: str) -> str:
    """Return the exact logged cargo-binstall invocation."""
    return (
        "cargo binstall <--no-confirm --disable-strategies=compile "
        f"--targets {target} --install-path {bin_dir.as_posix()} "
        f"--version ={version} merman-cli>"
    )


def test_manifest_preserves_input_defaults_and_selects_binary_only_merman() -> None:
    """Expose existing pins and install Merman with exact, binary-only settings."""
    manifest = _load_action()
    script = _install_script()

    assert manifest["runs"]["using"] == "composite"
    assert manifest["inputs"]["nixie-version"]["default"] == "1.1.0"
    assert manifest["inputs"]["merman-version"]["default"] == "0.7.0"
    assert manifest["inputs"]["python-version"]["default"] == "3.14"
    assert "cargo binstall" in script
    assert "--no-confirm" in script
    assert "--disable-strategies=compile" in script
    assert '--version "=${MERMAN_VERSION}"' in script
    assert '--install-path "$merman_bin_dir"' in script
    assert '--targets "$merman_target"' in script
    assert "cargo install" not in script
    for obsolete_downloader in ("curl", "shasum", "tar -", "merman_checksum"):
        assert obsolete_downloader not in script
    assert all(
        f"{runner_os}/{runner_arch}" in script
        for runner_os, runner_arch in SUPPORTED_TARGETS
    )


def test_action_prunes_the_callers_restored_cache_before_uv_install() -> None:
    """Remove restored script environments before the atomic uv installer."""
    steps = _load_action()["runs"]["steps"]
    names = [step.get("name") for step in steps]
    prune_index = names.index("Prune restored uv cache")
    install_index = names.index("Install Nixie and Merman CLI")

    assert prune_index < install_index, (
        f"prune step at {prune_index} must precede install step at {install_index}"
    )
    assert "prune_uv_cache.sh" in steps[prune_index]["run"], (
        "prune step must run the shared prune_uv_cache.sh script"
    )


def test_manifest_orders_probe_installer_and_pins_upstream_action() -> None:
    """Probe first, conditionally install the pinned tool, then install CLIs."""
    steps = _load_action()["runs"]["steps"]
    step_ids = [step.get("id") for step in steps]
    probe = _step_with_id(PROBE_STEP_ID)
    installer = next(
        step for step in steps if step.get("name") == "Install cargo-binstall"
    )
    cli_index = step_ids.index(INSTALL_STEP_ID)

    assert step_ids.index(PROBE_STEP_ID) < steps.index(installer) < cli_index
    assert probe["run"].splitlines() == [
        "if cargo binstall -V >/dev/null 2>&1; then",
        '  echo "installed=true" >> "${GITHUB_OUTPUT}"',
        "else",
        '  echo "installed=false" >> "${GITHUB_OUTPUT}"',
        "fi",
    ]
    assert installer["if"] == "steps.check-cargo-binstall.outputs.installed != 'true'"
    action, separator, pin = installer["uses"].partition("@")
    assert action == "cargo-bins/cargo-binstall"
    assert separator
    assert pin == BINSTALL_ACTION_PIN
    assert len(pin) == 40
    assert all(character in "0123456789abcdef" for character in pin)
    assert installer["with"] == {"version": BINSTALL_VERSION}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(
            _ProbeOptions(
                cargo_present=False,
                binstall_present=False,
                binstall_exit_status=127,
            ),
            "installed=false",
            id="cargo-absent",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=False,
                binstall_exit_status=127,
            ),
            "installed=false",
            id="subcommand-absent",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=True,
                binstall_exit_status=23,
            ),
            "installed=false",
            id="discoverable-shim-fails",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=True,
                binstall_exit_status=0,
            ),
            "installed=true",
            id="usable-subcommand",
        ),
    ],
)
def test_probe_writes_exact_result_and_swallows_unavailable_command(
    tmp_path: Path,
    options: _ProbeOptions,
    expected: str,
) -> None:
    """A missing or unusable Cargo subcommand is a false result, not step failure."""
    result = _run_probe(
        tmp_path,
        cargo_present=options.cargo_present,
        binstall_present=options.binstall_present,
        binstall_exit_status=options.binstall_exit_status,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == f"{expected}\n"
    expected_calls = ["cargo <binstall -V>"] if options.cargo_present else []
    if options.cargo_present and options.binstall_present:
        expected_calls.append("cargo-binstall <-V>")
    assert _calls(tmp_path, probe=True) == expected_calls


def test_cached_merman_is_reused_only_when_it_reports_the_requested_version(
    tmp_path: Path,
) -> None:
    """Reuse a working version-specific Merman cache and export its bin directory."""
    result = _run_install_script(tmp_path, _InstallOptions())

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    expected_path = (
        f"{(tmp_path / 'cache-home' / 'merman' / '0.7.0' / 'bin').as_posix()}\n"
        f"{(tmp_path / 'uv-bin').as_posix()}\n"
    )
    assert (tmp_path / "github-path").read_text(encoding="utf-8") == expected_path


def test_wrong_version_cache_is_reconciled_before_export(tmp_path: Path) -> None:
    """Replace a cached executable that reports a different Merman version."""
    options = _InstallOptions(cached_merman_version="0.6.9")
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert "Merman cache refresh" in result.stdout
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
        ),
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()[0] == (
        merman_bin_dir.as_posix()
    )


def test_unusable_cached_executable_is_reinstalled(tmp_path: Path) -> None:
    """Repair a cache entry whose version command exits unsuccessfully."""
    options = _InstallOptions(cached_merman_exit_status=41)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
    )


@pytest.mark.skipif(
    os.name == "nt", reason="Windows does not preserve POSIX executable mode bits"
)
def test_non_executable_cached_merman_is_reinstalled(tmp_path: Path) -> None:
    """A cached file without executable permission must not bypass installation."""
    options = _InstallOptions(cached_merman_executable=False)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert "Merman cache refresh" in result.stdout
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
    )


@pytest.mark.parametrize(
    "merman_version",
    ["0.8.3", "1.2.3"],
    ids=["new-minor", "new-major"],
)
def test_install_script_forwards_the_exact_requested_version(
    tmp_path: Path, merman_version: str
) -> None:
    """Install a numeric Merman version with source compilation disabled."""
    options = _InstallOptions(merman_cached=False, merman_version=merman_version)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / merman_version / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, merman_version, SUPPORTED_TARGETS[("Linux", "X64")]
        ),
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (merman_bin_dir / "merman-cli").is_file()


@pytest.mark.parametrize(
    "merman_version",
    ["0.7", "v0.7.0", "01.2.3", "0.7.0-rc.1", "not-a-version"],
)
def test_install_script_rejects_non_numeric_merman_versions_before_binstall(
    tmp_path: Path, merman_version: str
) -> None:
    """Require a three-component numeric version before invoking cargo-binstall."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, merman_version=merman_version),
    )

    assert result.returncode == 1
    assert "merman-version must use three numeric components" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("runner_os", "runner_arch", "target"),
    [
        pytest.param("Linux", "X64", "x86_64-unknown-linux-gnu", id="linux-x64"),
        pytest.param("macOS", "ARM64", "aarch64-apple-darwin", id="macos-arm64"),
        pytest.param("macOS", "X64", "x86_64-apple-darwin", id="macos-x64"),
        pytest.param("Windows", "X64", "x86_64-pc-windows-msvc", id="windows-x64"),
    ],
)
def test_install_script_selects_a_published_binary_target_and_exports_its_directory(
    tmp_path: Path, runner_os: str, runner_arch: str, target: str
) -> None:
    """Use each supported Merman release target and export the executable path."""
    options = _InstallOptions(
        merman_cached=False,
        runner_arch=runner_arch,
        runner_os=runner_os,
        uv_bin_dir_output=r"C:\Users\runneradmin\.local\bin"
        if runner_os == "Windows"
        else None,
    )
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", target
    )
    path_lines = (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()
    if runner_os == "Windows":
        assert path_lines == [
            r"C:\runner\cache\merman\0.7.0\bin",
            r"C:\Users\runneradmin\.local\bin",
        ]
    else:
        assert path_lines == [
            merman_bin_dir.as_posix(),
            (tmp_path / "uv-bin").as_posix(),
        ]


def test_install_failure_stops_nixie_and_does_not_export_path(tmp_path: Path) -> None:
    """A failed binary install must stop before Nixie and PATH publication."""
    options = _InstallOptions(merman_cached=False, merman_install_status=17)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 17
    assert "Merman binary installation failed" in result.stderr
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
        )
    ]
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        pytest.param(
            _InstallOptions(merman_cached=False, install_creates_merman=False),
            "cargo-binstall did not create a usable executable",
            id="missing-executable",
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, install_merman_executable=False),
            "cargo-binstall did not create a usable executable",
            id="non-executable-file",
            marks=pytest.mark.skipif(
                os.name == "nt",
                reason="Windows does not preserve POSIX executable mode bits",
            ),
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, installed_merman_version="0.6.9"),
            "installed merman-cli did not report requested version",
            id="wrong-installed-version",
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, installed_merman_exit_status=42),
            "installed merman-cli did not report requested version",
            id="unusable-installed-executable",
        ),
    ],
)
def test_install_script_rejects_missing_or_unusable_installed_merman(
    tmp_path: Path,
    options: _InstallOptions,
    message: str,
) -> None:
    """Do not continue when cargo-binstall fails to leave the requested CLI."""
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 1
    assert message in result.stderr
    assert not any(call.startswith("uv <tool install") for call in _calls(tmp_path))
    _assert_github_path_empty(tmp_path)


def test_install_script_rejects_an_unsupported_runner_target(tmp_path: Path) -> None:
    """Report unsupported operating-system and architecture pairs clearly."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, runner_arch="ARM64"),
    )

    assert result.returncode == 1
    assert "Unsupported Merman platform" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (_InstallOptions(nixie_version="1.1"), "nixie-version must use"),
        (_InstallOptions(python_version="3"), "python-version must use"),
    ],
)
def test_install_script_rejects_malformed_nixie_or_python_versions(
    tmp_path: Path,
    options: _InstallOptions,
    message: str,
) -> None:
    """Malformed Nixie or Python version pins stop before either installation."""
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 1
    assert message in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


def test_install_script_repairs_only_a_missing_nixie_shim(tmp_path: Path) -> None:
    """A missing Nixie shim should trigger exactly one forced reconciliation."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(nixie_shim_after_install=False),
    )

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
        "uv <tool install --force --python 3.14 nixie-cli==1.1.0>",
    ]


def test_install_script_fails_when_forced_reconciliation_leaves_no_shim(
    tmp_path: Path,
) -> None:
    """A failed Nixie shim repair should prevent PATH export."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(
            nixie_shim_after_install=False,
            nixie_shim_after_force=False,
        ),
    )

    assert result.returncode == 1
    assert "nixie was not installed" in result.stderr
    _assert_github_path_empty(tmp_path)


def test_install_script_forwards_nixie_and_python_overrides_through_uv(
    tmp_path: Path,
) -> None:
    """Nixie and Python overrides reach normal uv reconciliation unchanged."""
    options = _InstallOptions(nixie_version="1.2.3", python_version="3.13")
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.13 nixie-cli==1.2.3>",
        "uv <tool dir --bin>",
    ]


def test_install_script_stops_after_nixie_install_failure(tmp_path: Path) -> None:
    """Avoid shim repair and PATH export after normal Nixie reconciliation fails."""
    result = _run_install_script(tmp_path, _InstallOptions(uv_install_status=19))

    assert result.returncode == 19
    assert _calls(tmp_path) == ["uv <tool install --python 3.14 nixie-cli==1.1.0>"]
    _assert_github_path_empty(tmp_path)


def test_install_script_reports_missing_uv_before_merman_install(
    tmp_path: Path,
) -> None:
    """Stop before cargo-binstall when uv is unavailable."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(include_uv=False, merman_cached=False),
    )

    assert result.returncode == 1
    assert "uv is required to install nixie-cli" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


def test_install_script_reports_missing_cargo_before_merman_install(
    tmp_path: Path,
) -> None:
    """Stop before invoking cargo-binstall when Cargo is unavailable."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(include_cargo=False, merman_cached=False),
    )

    assert result.returncode == 1
    assert "cargo is required to run cargo-binstall for merman-cli" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)
