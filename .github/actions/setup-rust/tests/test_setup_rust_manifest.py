"""Tests covering the setup-rust composite action manifest."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
PINNED_BINSTALL_VERSION = "1.16.6"
PINNED_BINSTALL_TAG = f"v{PINNED_BINSTALL_VERSION}"
PINNED_BINSTALL_SHA256 = (
    "c2e963fbab3bdd8653b59c28d349bf85740cf4998e5e398d250dcd2884cd667d"
)


def _load_steps() -> list[dict[str, object]]:
    """Load the composite action steps from the setup-rust manifest."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _get_step(step_name: str) -> dict[str, object]:
    """Return a named composite action step, failing clearly if it is absent."""
    steps = _load_steps()
    step = next((step for step in steps if step.get("name") == step_name), None)
    assert step is not None, f"Missing setup-rust step: {step_name}"
    return step


def _install_binstall_run_script() -> str:
    """Return the cargo-binstall install step shell script."""
    install_step = _get_step("Install cargo-binstall")
    run_script = install_step.get("run")
    assert isinstance(run_script, str), "Install cargo-binstall step has no run script"
    return run_script


def _get_step_condition(step_name: str) -> str:
    """Return a named composite action step condition."""
    step = _get_step(step_name)
    condition = step.get("if")
    assert isinstance(condition, str), f"Step has no string condition: {step_name}"
    return condition


def _requires_bash() -> str:
    """Return a usable bash path or skip shell-fragment tests."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


def _write_executable(path: Path, content: str) -> None:
    """Write an executable test stub script."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_binstall_stubs(tmp_path: Path) -> Path:
    """Create command stubs used by the cargo-binstall install fragment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "$FAKE_CURL_ARGS"
output_path=""
previous=""
for arg in "$@"; do
  if [ "$previous" = "-o" ]; then
    output_path="$arg"
    break
  fi
  previous="$arg"
done
if [ -z "$output_path" ]; then
  echo "fake curl expected -o" >&2
  exit 2
fi
cat > "$output_path" <<'INSTALLER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${BINSTALL_VERSION:-}" > "$FAKE_INSTALLER_VERSION"
cat > "$FAKE_BIN_DIR/cargo-binstall" <<'BINSTALL'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "-V" ]; then
  printf '%s\\n' "${FAKE_BINSTALL_VERSION:-1.16.6}"
else
  echo "unexpected cargo-binstall invocation: $*" >&2
  exit 2
fi
BINSTALL
chmod +x "$FAKE_BIN_DIR/cargo-binstall"
INSTALLER
chmod +x "$output_path"
""",
    )
    _write_executable(
        bin_dir / "sha256sum",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '{PINNED_BINSTALL_SHA256}  %s\\n' "$1"
""",
    )
    return bin_dir


def _run_install_binstall_script(
    tmp_path: Path,
    *,
    installed_version: str = PINNED_BINSTALL_VERSION,
) -> subprocess.CompletedProcess[str]:
    """Run the cargo-binstall install fragment with deterministic stubs."""
    bash = _requires_bash()
    bin_dir = _write_binstall_stubs(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_BIN_DIR": bin_dir.as_posix(),
        "FAKE_BINSTALL_VERSION": installed_version,
        "FAKE_CURL_ARGS": (tmp_path / "curl-args").as_posix(),
        "FAKE_INSTALLER_VERSION": (tmp_path / "installer-version").as_posix(),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _install_binstall_run_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_manifest_exposes_toolchain_input() -> None:
    """The action should accept a toolchain override input."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    inputs = manifest.get("inputs", {})
    assert "toolchain" in inputs


def test_install_postgres_deps_is_linux_only() -> None:
    """Postgres packages should only install on Linux when requested."""
    condition = _get_step_condition("Install system dependencies")
    assert "runner.os == 'Linux'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition


def test_install_postgres_deps_windows_uses_choco() -> None:
    """Windows Postgres deps should install via Chocolatey when requested."""
    condition = _get_step_condition("Install libpq (headers + import library)")
    assert "runner.os == 'Windows'" in condition
    assert "inputs.install-postgres-deps == 'true'" in condition


def test_install_binstall_exports_version_pin() -> None:
    """The cargo-binstall installer should inherit the pinned version."""
    run_script = _install_binstall_run_script()
    run_lines = {line.strip() for line in run_script.splitlines()}
    assert f'export BINSTALL_VERSION="{PINNED_BINSTALL_TAG}"' in run_lines


def test_install_binstall_verifies_installed_version() -> None:
    """The cargo-binstall step should assert the installed pinned version."""
    run_script = _install_binstall_run_script()
    run_lines = {line.strip() for line in run_script.splitlines()}
    assert (
        f'if ! cargo-binstall -V | grep -q "{PINNED_BINSTALL_VERSION}"; then'
        in run_lines
    )


def test_install_binstall_script_exports_pin_to_installer(tmp_path: Path) -> None:
    """The install fragment should pass the pinned version to the child installer."""
    result = _run_install_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "installer-version").read_text(encoding="utf-8").strip() == (
        PINNED_BINSTALL_TAG
    )


def test_install_binstall_script_uses_pinned_installer_url(tmp_path: Path) -> None:
    """The install fragment should download the installer from the pinned tag."""
    result = _run_install_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    curl_args = (tmp_path / "curl-args").read_text(encoding="utf-8")
    assert (
        "https://raw.githubusercontent.com/cargo-bins/cargo-binstall/"
        f"{PINNED_BINSTALL_TAG}/install-from-binstall-release.sh"
    ) in curl_args


def test_install_binstall_script_logs_verified_version(tmp_path: Path) -> None:
    """The install fragment should log the verified cargo-binstall version."""
    result = _run_install_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    assert f"cargo-binstall {PINNED_BINSTALL_VERSION} verified" in result.stdout


def test_install_binstall_script_fails_on_version_mismatch(tmp_path: Path) -> None:
    """The install fragment should fail clearly when the installed version differs."""
    result = _run_install_binstall_script(tmp_path, installed_version="1.99.0")

    assert result.returncode != 0
    expected_error = (
        "cargo-binstall version verification failed: "
        f"expected {PINNED_BINSTALL_VERSION}"
    )
    assert expected_error in result.stderr
    assert "1.99.0" in result.stderr
