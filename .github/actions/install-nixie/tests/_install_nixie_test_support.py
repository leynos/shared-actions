"""Shared fixtures for the install-nixie action's shell-fragment tests.

The manifest loaders, the deterministic command stubs, and the invocation
helpers live here so ``test_action.py`` reads as a list of scenarios rather
than a mix of scenarios and scaffolding. The repository's production
dead-code scan excludes ``tests/``, which is why this support module must not
be imported by production code.
"""

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
    github_token: str | None = None
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
    """Return a Merman command stub that reports a fixed version.

    It also records the credential it inherited, because the action is
    expected to probe an unsigned executable with no token in its
    environment.
    """
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'merman token=<%s>\\n\' "${GITHUB_TOKEN-unset}" >> "$TOKEN_LOG"\n'
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
printf 'binstall token=<%s>\\n' "${GITHUB_TOKEN-unset}" >> "$TOKEN_LOG"
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
printf 'uv token=<%s>\\n' "${GITHUB_TOKEN-unset}" >> "$TOKEN_LOG"
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


def _write_stub_set(stubs_dir: Path, options: _InstallOptions) -> None:
    """Write the command stubs the scenario makes available on PATH."""
    stubs_dir.mkdir()
    if options.include_cargo:
        _write_cargo_stubs(stubs_dir)
    _write_cygpath_stub(stubs_dir)
    if options.include_uv:
        _write_uv_stub(stubs_dir)


def _seed_merman_cache(
    tmp_path: Path, options: _InstallOptions, executable: str
) -> None:
    """Place a cached Merman entry when the scenario starts warm."""
    if not options.merman_cached:
        return
    merman_path = (
        tmp_path / "cache-home" / "merman" / options.merman_version / "bin" / executable
    )
    merman_path.parent.mkdir(parents=True)
    _write_executable(
        merman_path,
        _merman_stub(
            options.cached_merman_version or options.merman_version,
            options.cached_merman_exit_status,
        ),
        executable=options.cached_merman_executable,
    )


def _runner_executable(options: _InstallOptions, *, tool: str) -> str:
    """Return a tool's executable name for the scenario's runner."""
    return f"{tool}.exe" if options.runner_os == "Windows" else tool


def _action_environment(
    tmp_path: Path, options: _InstallOptions, *, path: str
) -> dict[str, str]:
    """Return the process environment the installation fragment receives."""
    action_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GITHUB_TOKEN", "GH_TOKEN", "MERMAN_GITHUB_TOKEN"}
    }
    if options.github_token is not None:
        action_env["MERMAN_GITHUB_TOKEN"] = options.github_token
    uv_bin_dir = tmp_path / "uv-bin"
    return {
        **action_env,
        "BINSTALL_CREATES_MERMAN": str(options.install_creates_merman).lower(),
        "BINSTALL_INSTALL_STATUS": str(options.merman_install_status),
        "BINSTALL_MERMAN_EXECUTABLE": str(options.install_merman_executable).lower(),
        "BINSTALL_MERMAN_SCRIPT": _merman_stub(
            options.installed_merman_version or options.merman_version,
            options.installed_merman_exit_status,
        ),
        "CALLS_PATH": (tmp_path / "calls").as_posix(),
        "GITHUB_PATH": (tmp_path / "github-path").as_posix(),
        "TOKEN_LOG": (tmp_path / "token-log").as_posix(),
        "MERMAN_EXECUTABLE": _runner_executable(options, tool="merman-cli"),
        "MERMAN_VERSION": options.merman_version,
        "MERMAN_WINDOWS_BIN_DIR": (
            rf"C:\runner\cache\merman\{options.merman_version}\bin"
        ),
        "NIXIE_SHIM_AFTER_FORCE": str(options.nixie_shim_after_force).lower(),
        "NIXIE_SHIM_AFTER_INSTALL": str(options.nixie_shim_after_install).lower(),
        "NIXIE_VERSION": options.nixie_version,
        "PATH": path,
        "PYTHON_VERSION": options.python_version,
        "RUNNER_ARCH": options.runner_arch,
        "RUNNER_OS": options.runner_os,
        "UV_BIN_DIR": uv_bin_dir.as_posix(),
        "UV_BIN_DIR_OUTPUT": options.uv_bin_dir_output or uv_bin_dir.as_posix(),
        "UV_EXECUTABLE": _runner_executable(options, tool="nixie"),
        "UV_INSTALL_STATUS": str(options.uv_install_status),
        "XDG_CACHE_HOME": (tmp_path / "cache-home").as_posix(),
    }


def _install_environment(
    tmp_path: Path, options: _InstallOptions, bash: str
) -> dict[str, str]:
    """Prepare deterministic CLI stubs, cache contents and action variables."""
    stubs_dir = tmp_path / "stubs"
    _write_stub_set(stubs_dir, options)
    _seed_merman_cache(
        tmp_path, options, _runner_executable(options, tool="merman-cli")
    )
    prerequisites_present = options.include_uv and options.include_cargo
    path = _isolated_path(stubs_dir, bash) if prerequisites_present else str(stubs_dir)
    return _action_environment(tmp_path, options, path=path)


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


def _token_observations(tmp_path: Path) -> list[str]:
    """Return the credentials each Merman binary saw, in invocation order."""
    return (tmp_path / "token-log").read_text(encoding="utf-8").splitlines()
