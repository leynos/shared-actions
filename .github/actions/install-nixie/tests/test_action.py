"""Verify the cache-aware, checksum-verified install-nixie action contract."""

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
_MERMAN_CHECKSUM = "dfdc2a978a884aa5a2ad5b85285fb5175cb435e82cf96efa860a550749e09d99"
_WINDOWS_MERMAN_CHECKSUM = (
    "51f4898058d7bae48255a15663cafc14fcee3e352f271a916b2c057587070977"
)


@dataclass(frozen=True)
class _InstallOptions:
    """Describe one deterministic installation scenario."""

    merman_cached: bool = True
    merman_version: str = "0.7.0"
    nixie_shim_after_install: bool = True
    nixie_shim_after_force: bool = True
    nixie_version: str = "1.1.0"
    python_version: str = "3.14"
    actual_checksum: str = _MERMAN_CHECKSUM
    curl_status: int = 0
    include_uv: bool = True
    runner_arch: str = "X64"
    runner_os: str = "Linux"
    uv_install_status: int = 0


def _load_action() -> dict[str, typ.Any]:
    """Load the install-nixie action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _install_script() -> str:
    """Return the action's installation shell fragment."""
    steps = _load_action()["runs"]["steps"]
    assert len(steps) == 1, "install-nixie should have one atomic install step"
    run_script = steps[0].get("run")
    assert isinstance(run_script, str), "install step must define a shell script"
    return run_script


def _write_executable(path: Path, content: str) -> None:
    """Write an executable command stub."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_download_stubs(stubs_dir: Path) -> None:
    """Write the deterministic stubs needed for Merman cache-miss scenarios."""
    _write_curl_stub(stubs_dir)
    _write_checksum_stub(stubs_dir)
    _write_unix_archive_stub(stubs_dir)
    _write_windows_archive_stubs(stubs_dir)


def _write_curl_stub(stubs_dir: Path) -> None:
    """Write a curl stub that records the release archive download."""
    _write_executable(
        stubs_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'curl <%s>\\n' "$*" >> "$CALLS_PATH"
output_path=""
while (($#)); do
  if [[ "$1" == "--output" ]]; then
    output_path="$2"
    shift 2
  else
    shift
  fi
done
if [[ -n "$output_path" ]]; then
  printf 'verified archive' > "$output_path"
fi
exit "$CURL_STATUS"
""",
    )


def _write_checksum_stub(stubs_dir: Path) -> None:
    """Write a shasum stub that returns the scenario's Merman checksum."""
    _write_executable(
        stubs_dir / "shasum",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'shasum <%s>\\n' "$*" >> "$CALLS_PATH"
printf '%s  %s\\n' "$MERMAN_ACTUAL_CHECKSUM" "${@: -1}"
""",
    )


def _write_unix_archive_stub(stubs_dir: Path) -> None:
    """Write a tar stub that extracts Unix Merman executables under archive roots."""
    _write_executable(
        stubs_dir / "tar",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'tar <%s>\\n' "$*" >> "$CALLS_PATH"
destination=""
while (($#)); do
  if [[ "$1" == "-C" ]]; then
    destination="$2"
    shift 2
  else
    shift
  fi
done
for archive_root in \
  merman-cli-x86_64-unknown-linux-gnu \
  merman-cli-aarch64-apple-darwin \
  merman-cli-x86_64-apple-darwin; do
  mkdir -p "$destination/$archive_root"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$destination/$archive_root/merman-cli"
  chmod +x "$destination/$archive_root/merman-cli"
done
""",
    )


def _write_windows_archive_stubs(stubs_dir: Path) -> None:
    """Write cygpath and PowerShell stubs for Windows archive handling."""
    _write_executable(
        stubs_dir / "cygpath",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'cygpath <%s>\\n' "$*" >> "$CALLS_PATH"
printf '%s\\n' "${@: -1}"
""",
    )
    _write_executable(
        stubs_dir / "powershell.exe",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'powershell.exe <%s>\\n' "$*" >> "$CALLS_PATH"
if [[ "$*" == *"Get-FileHash"* ]]; then
  printf '%s\\n' "$MERMAN_ACTUAL_CHECKSUM"
  exit 0
fi
mkdir -p "$MERMAN_EXTRACT_DIR"
printf '#!/usr/bin/env bash\\nexit 0\\n' > "$MERMAN_EXTRACT_DIR/merman-cli.exe"
chmod +x "$MERMAN_EXTRACT_DIR/merman-cli.exe"
""",
    )


def _write_uv_stub(stubs_dir: Path) -> None:
    """Write a uv stub that can model normal and forced shim reconciliation."""
    _write_executable(
        stubs_dir / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv <%s>\\n' "$*" >> "$CALLS_PATH"
if [[ "${1:-}" == "tool" && "${2:-}" == "dir" && "${3:-}" == "--bin" ]]; then
  printf '%s\\n' "$UV_BIN_DIR"
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
  if [[ "$should_create" == true ]]; then
    mkdir -p "$UV_BIN_DIR"
    printf '#!/usr/bin/env bash\\nexit 0\\n' > "$UV_BIN_DIR/$UV_EXECUTABLE"
    chmod +x "$UV_BIN_DIR/$UV_EXECUTABLE"
  fi
fi
""",
    )


def _run_install_script(
    tmp_path: Path,
    options: _InstallOptions,
) -> subprocess.CompletedProcess[str]:
    """Execute the install fragment against deterministic command stubs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    stubs_dir = tmp_path / "stubs"
    stubs_dir.mkdir()
    calls_path = tmp_path / "calls"
    github_path = tmp_path / "github-path"
    cargo_home = tmp_path / "cargo-home"
    is_windows_runner = options.runner_os == "Windows"
    merman_executable = "merman-cli.exe" if is_windows_runner else "merman-cli"
    nixie_executable = "nixie.exe" if is_windows_runner else "nixie"
    merman_path = cargo_home / "bin" / merman_executable
    if options.merman_cached:
        merman_path.parent.mkdir(parents=True)
        _write_executable(merman_path, "#!/usr/bin/env bash\nexit 0\n")
    else:
        _write_download_stubs(stubs_dir)
    if options.include_uv:
        _write_uv_stub(stubs_dir)

    uv_bin_dir = tmp_path / "uv-bin"
    path = f"{stubs_dir}{os.pathsep}{os.environ['PATH']}"
    if not options.include_uv:
        path = stubs_dir.as_posix()
    env = {
        **os.environ,
        "CALLS_PATH": calls_path.as_posix(),
        "CARGO_HOME": cargo_home.as_posix(),
        "CURL_STATUS": str(options.curl_status),
        "GITHUB_PATH": github_path.as_posix(),
        "MERMAN_ACTUAL_CHECKSUM": options.actual_checksum,
        "MERMAN_VERSION": options.merman_version,
        "NIXIE_SHIM_AFTER_FORCE": str(options.nixie_shim_after_force).lower(),
        "NIXIE_SHIM_AFTER_INSTALL": str(options.nixie_shim_after_install).lower(),
        "NIXIE_VERSION": options.nixie_version,
        "PATH": path,
        "PYTHON_VERSION": options.python_version,
        "RUNNER_ARCH": options.runner_arch,
        "RUNNER_OS": options.runner_os,
        "UV_BIN_DIR": uv_bin_dir.as_posix(),
        "UV_EXECUTABLE": nixie_executable,
        "UV_INSTALL_STATUS": str(options.uv_install_status),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _install_script()],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _calls(tmp_path: Path) -> list[str]:
    """Return recorded external command calls."""
    calls_path = tmp_path / "calls"
    if not calls_path.exists():
        return []
    return calls_path.read_text(encoding="utf-8").splitlines()


def _assert_github_path_empty(tmp_path: Path) -> None:
    """Assert that the action did not export a tool directory."""
    github_path = tmp_path / "github-path"
    assert not github_path.exists() or github_path.read_text(encoding="utf-8") == ""


def test_manifest_exposes_pinned_version_inputs_and_verified_assets() -> None:
    """The action should expose its stable inputs and checksum-pinned assets."""
    manifest = _load_action()
    script = _install_script()

    assert manifest["runs"]["using"] == "composite"
    assert manifest["inputs"]["nixie-version"]["default"] == "1.1.0"
    assert manifest["inputs"]["merman-version"]["default"] == "0.7.0"
    assert manifest["inputs"]["python-version"]["default"] == "3.14"
    assert "cargo install" not in script
    assert "cargo binstall" not in script
    assert "Linux/X64" in script
    assert "macOS/ARM64" in script
    assert "macOS/X64" in script
    assert "Windows/X64" in script
    assert _MERMAN_CHECKSUM in script
    assert _WINDOWS_MERMAN_CHECKSUM in script
    assert "c73a9f676b2f9ec5b81ec805253f39f160b1d76a503c80408bea72fa017fb8f1" in script
    assert "5c61d806c9cdb1b16062797955fb51849d5df7789e0dc8ea4c54e22d61b865ae" in script


def test_install_script_uses_cached_merman_and_normal_nixie_reconciliation(
    tmp_path: Path,
) -> None:
    """A complete warm cache should not download Merman or force Nixie."""
    result = _run_install_script(tmp_path, _InstallOptions())

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    expected_github_path = (
        f"{(tmp_path / 'cargo-home' / 'bin').as_posix()}\n"
        f"{(tmp_path / 'uv-bin').as_posix()}\n"
    )
    assert (tmp_path / "github-path").read_text(
        encoding="utf-8"
    ) == expected_github_path


def test_install_script_downloads_only_the_verified_official_merman_asset(
    tmp_path: Path,
) -> None:
    """A cache miss should install a checksum-verified official Merman binary."""
    result = _run_install_script(tmp_path, _InstallOptions(merman_cached=False))

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert calls[0].endswith(
        "https://github.com/Latias94/merman/releases/download/v0.7.0/"
        "merman-cli-x86_64-unknown-linux-gnu.tar.xz>"
    )
    assert calls[1].startswith("shasum <-a 256 ")
    assert calls[2].startswith("tar <-xJf ")
    assert calls[3:] == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (tmp_path / "cargo-home" / "bin" / "merman-cli").is_file()


def test_install_script_installs_the_verified_windows_release_and_shims(
    tmp_path: Path,
) -> None:
    """Install the Windows release archive and executable-aware Nixie shim."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(
            actual_checksum=_WINDOWS_MERMAN_CHECKSUM,
            merman_cached=False,
            runner_os="Windows",
        ),
    )

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert calls[0].endswith(
        "https://github.com/Latias94/merman/releases/download/v0.7.0/"
        "merman-cli-x86_64-pc-windows-msvc.zip>"
    )
    assert [call.split(" <", maxsplit=1)[0] for call in calls[1:5]] == [
        "cygpath",
        "powershell.exe",
        "cygpath",
        "powershell.exe",
    ]
    assert calls[5:] == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (tmp_path / "cargo-home" / "bin" / "merman-cli.exe").is_file()
    assert (tmp_path / "uv-bin" / "nixie.exe").is_file()


def test_install_script_stops_when_the_merman_checksum_differs(tmp_path: Path) -> None:
    """Prevent untrusted Merman installation when the checksum differs."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, actual_checksum="0" * 64),
    )

    assert result.returncode == 1
    assert "Merman checksum mismatch" in result.stderr
    assert len(_calls(tmp_path)) == 2
    assert not any(call.startswith("uv <tool install") for call in _calls(tmp_path))
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize("merman_version", ["0.8.0", "not-a-version"])
def test_install_script_rejects_unverified_merman_versions(
    tmp_path: Path,
    merman_version: str,
) -> None:
    """Unsupported Merman versions should fail before any installer runs."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, merman_version=merman_version),
    )

    assert result.returncode == 1
    assert "only 0.7.0 has a pinned release checksum" in result.stderr
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


def test_install_script_rejects_an_unverified_merman_platform(tmp_path: Path) -> None:
    """Reject an unsupported runner pair before tool reconciliation."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, runner_arch="ARM64"),
    )

    assert result.returncode == 1
    assert "Unsupported Merman platform" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


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


def test_install_script_propagates_supported_nixie_and_python_overrides(
    tmp_path: Path,
) -> None:
    """Nixie and Python overrides should reach normal reconciliation unchanged."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(nixie_version="1.2.3", python_version="3.13"),
    )

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


def test_install_script_reports_missing_uv_before_downloading_merman(
    tmp_path: Path,
) -> None:
    """Stop before Merman download when uv is unavailable."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(include_uv=False, merman_cached=False),
    )

    assert result.returncode == 1
    assert "uv is required to install nixie-cli" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)
