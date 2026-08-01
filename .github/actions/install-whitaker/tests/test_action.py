"""Contract tests for the install-whitaker composite action."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


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


def _run_install_script(
    tmp_path: Path,
    *,
    binstall_available: bool,
    installer_present: bool = False,
    fail_binstall: bool = False,
    fail_install: bool = False,
    fail_installer: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the installation fragment with deterministic command stubs."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    cargo_home = tmp_path / "cargo-home"
    bin_dir = cargo_home / "bin"
    bin_dir.mkdir(parents=True)
    cargo_log = tmp_path / "cargo.log"
    installer_log = tmp_path / "installer.log"
    _write_cargo_stub(bin_dir)
    if installer_present:
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
        "PATH": f"/usr/bin{os.pathsep}/bin",
        "CARGO_HOME": cargo_home.as_posix(),
        "BINSTALL_AVAILABLE": str(binstall_available).lower(),
        "CARGO_LOG": cargo_log.as_posix(),
        "FAIL_BINSTALL": str(fail_binstall).lower(),
        "FAIL_INSTALL": str(fail_install).lower(),
        "FAIL_INSTALLER": str(fail_installer).lower(),
        "FAKE_BIN_DIR": bin_dir.as_posix(),
        "INSTALLER_LOG": installer_log.as_posix(),
        "WHITAKER_INSTALLER_VERSION": "0.2.6",
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
        [bash, "-c", _install_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def test_manifest_exposes_version_and_cache_contract() -> None:
    """The manifest should expose the pin and cache the installer artefacts."""
    manifest = _load_manifest()

    assert manifest["inputs"] == {
        "installer-version": {
            "description": "Version of whitaker-installer to install",
            "required": False,
            "default": "0.2.6",
        }
    }
    runs = manifest["runs"]
    assert isinstance(runs, dict)
    steps = typ.cast("list[dict[str, object]]", runs["steps"])
    cache_step = steps[0]
    assert cache_step["uses"] == (
        "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    )
    cache_config = typ.cast("dict[str, str]", cache_step["with"])
    assert "~/.cargo/bin/whitaker-installer" in cache_config["path"]
    assert "~/.cache/cargo-binstall" in cache_config["path"]
    assert "${{ inputs.installer-version }}" in cache_config["key"]


def test_installs_with_cargo_binstall_when_available(tmp_path: Path) -> None:
    """cargo-binstall should be preferred when its subcommand is available."""
    result = _run_install_script(tmp_path, binstall_available=True)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
        "binstall --version",
        "binstall --no-confirm --locked whitaker-installer@0.2.6",
    ]
    assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
        "suite installed\n"
    )


def test_falls_back_to_cargo_install(tmp_path: Path) -> None:
    """Cargo should build whitaker-installer when cargo-binstall is unavailable."""
    result = _run_install_script(tmp_path, binstall_available=False)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines() == [
        "binstall --version",
        "install --locked whitaker-installer --version 0.2.6",
    ]
    assert "cargo-binstall unavailable" in result.stdout


def test_reuses_cached_installer(tmp_path: Path) -> None:
    """A restored installer should avoid both Cargo installation paths."""
    result = _run_install_script(
        tmp_path,
        binstall_available=False,
        installer_present=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "cargo.log").exists()
    assert (tmp_path / "installer.log").read_text(encoding="utf-8") == (
        "suite installed\n"
    )


def test_reports_cargo_binstall_failure(tmp_path: Path) -> None:
    """A cargo-binstall failure should be actionable and non-zero."""
    result = _run_install_script(
        tmp_path,
        binstall_available=True,
        fail_binstall=True,
    )

    assert result.returncode != 0
    assert "cargo binstall failed while installing whitaker-installer" in result.stderr


def test_reports_cargo_install_failure(tmp_path: Path) -> None:
    """A cargo install fallback failure should be actionable and non-zero."""
    result = _run_install_script(
        tmp_path,
        binstall_available=False,
        fail_install=True,
    )

    assert result.returncode != 0
    assert "cargo install failed while installing whitaker-installer" in (result.stderr)


def test_reports_whitaker_installer_failure(tmp_path: Path) -> None:
    """A suite installer failure should be actionable and non-zero."""
    result = _run_install_script(
        tmp_path,
        binstall_available=True,
        fail_installer=True,
    )

    assert result.returncode != 0
    assert "whitaker-installer failed while installing the Dylint suite" in (
        result.stderr
    )
