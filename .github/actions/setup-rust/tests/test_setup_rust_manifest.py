"""Tests covering the setup-rust composite action manifest."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
PINNED_BINSTALL_VERSION = "1.19.1"
PINNED_BINSTALL_TAG = f"v{PINNED_BINSTALL_VERSION}"
PINNED_BINSTALL_SHA256 = (
    "d3a93702160e0ec03e2a4e996855db1f01adee801fb84a43add24e0877ef8eae"
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


def _write_binstall_stubs(stubs_dir: Path) -> None:
    """Create command stubs used by the cargo-binstall install fragment."""
    stubs_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(
        stubs_dir / "curl",
        f"""#!/usr/bin/env bash
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
printf '%s\\n' "${{BINSTALL_VERSION:-}}" > "$FAKE_INSTALLER_VERSION"
mkdir -p "$FAKE_BIN_DIR"
cat > "$FAKE_BIN_DIR/cargo-binstall" <<'BINSTALL'
#!/usr/bin/env bash
set -euo pipefail
if [ "${{1:-}}" = "-V" ]; then
  printf '%s\\n' "${{FAKE_BINSTALL_VERSION:-{PINNED_BINSTALL_VERSION}}}"
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
        stubs_dir / "sha256sum",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '{PINNED_BINSTALL_SHA256}  %s\\n' "$1"
""",
    )


def _default_cargo_home(tmp_path: Path) -> Path:
    """Return the default isolated CARGO_HOME for a test run."""
    return tmp_path / ".cargo"


def _run_install_binstall_script(
    tmp_path: Path,
    cargo_home: Path,
    github_path: Path | None,
    *,
    installed_version: str = PINNED_BINSTALL_VERSION,
) -> subprocess.CompletedProcess[str]:
    """Run the cargo-binstall install fragment with deterministic stubs."""
    bash = _requires_bash()
    stubs_dir = tmp_path / "stubs"
    _write_binstall_stubs(stubs_dir)
    cargo_home_bin = cargo_home / "bin"
    env = {
        **os.environ,
        "HOME": tmp_path.as_posix(),
        "PATH": f"{stubs_dir}{os.pathsep}{os.environ['PATH']}",
        "CARGO_HOME": cargo_home.as_posix(),
        "FAKE_BIN_DIR": cargo_home_bin.as_posix(),
        "FAKE_BINSTALL_VERSION": installed_version,
        "FAKE_CURL_ARGS": (tmp_path / "curl-args").as_posix(),
        "FAKE_INSTALLER_VERSION": (tmp_path / "installer-version").as_posix(),
    }
    if github_path is not None:
        env["GITHUB_PATH"] = github_path.as_posix()
    else:
        env.pop("GITHUB_PATH", None)
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _install_binstall_run_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_get_step_reports_missing_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing manifest steps should fail with a contextual assertion."""
    monkeypatch.setattr(__name__ + "._load_steps", lambda: [{"name": "Other Step"}])

    with pytest.raises(
        AssertionError,
        match="Missing setup-rust step: Nonexistent Step",
    ):
        _get_step("Nonexistent Step")


def test_get_step_condition_requires_string_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step conditions should be present and string-valued."""
    step_name = "Install system dependencies"
    monkeypatch.setattr(__name__ + "._load_steps", lambda: [{"name": step_name}])

    with pytest.raises(AssertionError, match="Step has no string condition"):
        _get_step_condition(step_name)


def test_install_binstall_run_script_requires_string_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cargo-binstall step should expose a shell run script."""
    monkeypatch.setattr(
        __name__ + "._load_steps",
        lambda: [{"name": "Install cargo-binstall"}],
    )

    with pytest.raises(
        AssertionError,
        match="Install cargo-binstall step has no run script",
    ):
        _install_binstall_run_script()


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
    assert 'binstall_ver="${BINSTALL_VERSION#v}"' in run_lines
    assert 'if ! "$cargo_binstall" -V | grep -qF "$binstall_ver"; then' in run_lines


def test_install_binstall_resolves_cargo_home_bin() -> None:
    """The script should resolve the active Cargo bin directory."""
    run_script = _install_binstall_run_script()
    run_lines = {line.strip() for line in run_script.splitlines()}
    assert 'cargo_home_bin="${CARGO_HOME:-$HOME/.cargo}/bin"' in run_lines
    assert 'cargo_binstall="$cargo_home_bin/cargo-binstall"' in run_lines


def test_install_binstall_script_exports_pin_to_installer(tmp_path: Path) -> None:
    """The install fragment should pass the pinned version to the child installer."""
    result = _run_install_binstall_script(tmp_path, _default_cargo_home(tmp_path), None)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "installer-version").read_text(encoding="utf-8").strip() == (
        PINNED_BINSTALL_TAG
    )


def test_install_binstall_script_uses_pinned_installer_url(tmp_path: Path) -> None:
    """The install fragment should download the installer from the pinned tag."""
    result = _run_install_binstall_script(tmp_path, _default_cargo_home(tmp_path), None)

    assert result.returncode == 0, result.stderr
    curl_args = (tmp_path / "curl-args").read_text(encoding="utf-8")
    assert (
        "https://raw.githubusercontent.com/cargo-bins/cargo-binstall/"
        f"{PINNED_BINSTALL_TAG}/install-from-binstall-release.sh"
    ) in curl_args


def test_install_binstall_script_logs_verified_version(tmp_path: Path) -> None:
    """The install fragment should log the verified cargo-binstall version."""
    result = _run_install_binstall_script(tmp_path, _default_cargo_home(tmp_path), None)

    assert result.returncode == 0, result.stderr
    assert f"cargo-binstall {PINNED_BINSTALL_VERSION} verified" in result.stdout


def test_install_binstall_script_fails_on_version_mismatch(tmp_path: Path) -> None:
    """The install fragment should fail clearly when the installed version differs."""
    result = _run_install_binstall_script(
        tmp_path,
        _default_cargo_home(tmp_path),
        None,
        installed_version="1.99.0",
    )

    assert result.returncode != 0
    expected_error = (
        "cargo-binstall version verification failed: "
        f"expected {PINNED_BINSTALL_VERSION}"
    )
    assert expected_error in result.stderr
    assert "1.99.0" in result.stderr


def test_install_binstall_script_verifies_with_custom_cargo_home(
    tmp_path: Path,
) -> None:
    """The install fragment should verify cargo-binstall under a custom CARGO_HOME."""
    custom_cargo_home = tmp_path / "isolated-cargo"
    result = _run_install_binstall_script(tmp_path, custom_cargo_home, None)

    assert result.returncode == 0, result.stderr
    assert (custom_cargo_home / "bin" / "cargo-binstall").is_file()
    assert f"cargo-binstall {PINNED_BINSTALL_VERSION} verified" in result.stdout


def test_install_binstall_script_appends_cargo_bin_to_github_path(
    tmp_path: Path,
) -> None:
    """The fragment should append the active Cargo bin dir to GITHUB_PATH."""
    custom_cargo_home = tmp_path / "isolated-cargo"
    github_path = tmp_path / "github-path"
    existing_entry = "/preexisting/path"
    github_path.write_text(f"{existing_entry}\n", encoding="utf-8")
    result = _run_install_binstall_script(tmp_path, custom_cargo_home, github_path)

    assert result.returncode == 0, result.stderr
    entries = github_path.read_text(encoding="utf-8").splitlines()
    expected_entry = (custom_cargo_home / "bin").as_posix()
    assert entries == [existing_entry, expected_entry]


def test_install_binstall_script_skips_github_path_when_unset(
    tmp_path: Path,
) -> None:
    """The fragment should tolerate a missing GITHUB_PATH (set -u safety)."""
    result = _run_install_binstall_script(tmp_path, _default_cargo_home(tmp_path), None)

    assert result.returncode == 0, result.stderr


def test_install_binstall_script_does_not_duplicate_path_entry(
    tmp_path: Path,
) -> None:
    """The PATH deduplication guard must not add the bin dir a second time."""
    cargo_home = tmp_path / ".cargo"
    cargo_home_bin = (cargo_home / "bin").as_posix()
    bash = _requires_bash()
    stubs_dir = tmp_path / "stubs"
    _write_binstall_stubs(stubs_dir)
    parent_path = os.environ["PATH"]
    env = {
        **os.environ,
        "HOME": tmp_path.as_posix(),
        "PATH": os.pathsep.join([cargo_home_bin, str(stubs_dir), parent_path]),
        "CARGO_HOME": cargo_home.as_posix(),
        "FAKE_BIN_DIR": cargo_home_bin,
        "FAKE_BINSTALL_VERSION": PINNED_BINSTALL_VERSION,
        "FAKE_CURL_ARGS": (tmp_path / "curl-args").as_posix(),
        "FAKE_INSTALLER_VERSION": (tmp_path / "installer-version").as_posix(),
    }
    env.pop("GITHUB_PATH", None)
    result = subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", _install_binstall_run_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # The bin dir was already on PATH; re-run with a trailing echo to capture
    # the post-script PATH and confirm no duplicate entry was introduced.
    inspect_script = _install_binstall_run_script() + '\necho "RESULT_PATH=$PATH"'
    result2 = subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", inspect_script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result2.returncode == 0, result2.stderr
    path_line = next(
        (
            line
            for line in result2.stdout.splitlines()
            if line.startswith("RESULT_PATH=")
        ),
        "",
    )
    resulting_path = path_line.removeprefix("RESULT_PATH=")
    entries = resulting_path.split(os.pathsep)
    assert entries.count(cargo_home_bin) == 1, (
        f"Expected {cargo_home_bin!r} to appear exactly once; got: {resulting_path!r}"
    )
