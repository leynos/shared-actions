"""Behavioural contracts for CodeScene coverage upload mode."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"

INSTALLER_BODY = "#!/usr/bin/env bash\ntrue\n"


def _steps() -> list[dict[str, object]]:
    """Return the composite action steps."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _step_by_name(name: str) -> dict[str, object]:
    """Return the first step with the given name."""
    return next(step for step in _steps() if step.get("name") == name)


def _run_fragment(
    tmp_path: Path,
    script: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a composite-action bash fragment in a subprocess."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    full_env = os.environ | (env or {})
    # GitHub Actions runs `run:` steps with `bash -e -o pipefail`; mirror
    # those flags so failure propagation matches the real step.
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", script],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=full_env,
        text=True,
    )


def _stub_path(tmp_path: Path) -> str:
    """Return a PATH that puts *tmp_path* ahead of the system directories."""
    return f"{tmp_path}{os.pathsep}{os.environ['PATH']}"


def _write_stub_cli(tmp_path: Path, *, exit_status: int = 0) -> None:
    """Write a cs-coverage stub that records its argv and exits."""
    cli = tmp_path / "cs-coverage"
    cli.write_text(
        f"#!/usr/bin/env bash\nprintf 'arguments: %s\\n' \"$*\"\nexit {exit_status}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)


def _write_curl_stub(tmp_path: Path) -> None:
    """Write a curl stub that writes the installer to its ``-o`` target."""
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "installed=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -o) installed="$2"; shift 2 ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "printf '%s\\n' '#!/usr/bin/env bash' 'true' > \"$installed\"\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)


def _read_outputs(path: Path) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file into key/value pairs."""
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
    )


def _upload_script() -> str:
    """Return the upload step run body with expressions resolved to defaults."""
    step = _step_by_name("Upload coverage to CodeScene")
    return (
        str(step["run"])
        .replace("${{ steps.cov-file.outputs.path }}", "coverage.xml")
        .replace("${{ inputs.format }}", "cobertura")
    )


def _run_download_installer(
    tmp_path: Path,
    *,
    cli_version: str = "2.3.1",
    checksum: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the download-installer step with a curl stub and optional checksum."""
    _write_curl_stub(tmp_path)
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env: dict[str, str] = {
        "CLI_VERSION": cli_version,
        "GITHUB_OUTPUT": str(output),
        "PATH": _stub_path(tmp_path),
    }
    if checksum is not None:
        env["CODESCENE_CLI_SHA256"] = checksum
    script = str(_step_by_name("Download installer")["run"])
    return _run_fragment(tmp_path, script, env=env)


def test_upload_missing_coverage_file_fails_with_clear_message(
    tmp_path: Path,
) -> None:
    """A missing coverage file aborts the upload with a clear diagnostic."""
    _write_stub_cli(tmp_path)

    result = _run_fragment(
        tmp_path, _upload_script(), env={"PATH": _stub_path(tmp_path)}
    )

    assert result.returncode == 1
    assert "Coverage file not found!" in result.stderr
    assert "coverage.xml" in result.stderr


def test_upload_invokes_cli_with_cobertura_arguments(tmp_path: Path) -> None:
    """A present report is uploaded with the cobertura line-coverage flags."""
    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    _write_stub_cli(tmp_path)
    result = _run_fragment(
        tmp_path,
        _upload_script(),
        env={"PATH": _stub_path(tmp_path)},
    )

    assert result.returncode == 0
    assert (
        "arguments: upload --format cobertura --metric line-coverage coverage.xml"
        in result.stdout
    )


def test_upload_fails_when_cli_missing(tmp_path: Path) -> None:
    """The upload aborts before uploading when the CLI is not installed."""
    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    result = _run_fragment(
        tmp_path,
        _upload_script(),
        env={"PATH": str(tmp_path)},
    )

    assert result.returncode == 1
    assert "cs-coverage CLI not found" in result.stderr


def test_upload_step_restricted_to_upload_mode_with_token() -> None:
    """The upload step only runs for upload mode when a token is present."""
    condition = str(_step_by_name("Upload coverage to CodeScene").get("if", ""))
    assert "env.CS_ACCESS_TOKEN != ''" in condition
    assert "inputs.mode == 'upload'" in condition


def test_install_runs_installer_with_version_and_removes_script(
    tmp_path: Path,
) -> None:
    """The CLI installer is executed with the version and then removed."""
    script = str(_step_by_name("Install CodeScene Coverage CLI")["run"])
    installer = tmp_path / "installer.sh"
    args_log = tmp_path / "installer-args"
    installer.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > installer-args\n",
        encoding="utf-8",
    )
    installer.chmod(0o755)
    script = script.replace("${{ steps.installer.outputs.script }}", str(installer))

    result = _run_fragment(tmp_path, script, env={"CLI_VERSION": "2.1.0"})

    assert result.returncode == 0
    assert args_log.read_text(encoding="utf-8").strip() == "-y 2.1.0"
    assert not installer.exists()


def test_install_step_skipped_without_token_or_on_cache_hit() -> None:
    """The install step honours the token and cache-hit guards."""
    condition = str(_step_by_name("Install CodeScene Coverage CLI").get("if", ""))
    assert "env.CS_ACCESS_TOKEN != ''" in condition
    assert "steps.cs-cache.outputs.cache-hit != 'true'" in condition


def test_download_installer_verifies_checksum_when_provided(
    tmp_path: Path,
) -> None:
    """A matching installer checksum lets the download succeed."""
    result = _run_download_installer(
        tmp_path,
        checksum=hashlib.sha256(INSTALLER_BODY.encode()).hexdigest(),
    )

    assert result.returncode == 0
    outputs = _read_outputs(tmp_path / "github-output")
    assert outputs["version"] == "2.3.1"
    assert outputs["major_minor"] == "2.3"
    installer = Path(outputs["script"])
    assert installer.read_text(encoding="utf-8") == INSTALLER_BODY


def test_download_installer_rejects_mismatched_checksum(tmp_path: Path) -> None:
    """A mismatched installer checksum aborts the download."""
    result = _run_download_installer(tmp_path, checksum="0" * 64)

    assert result.returncode != 0


def test_download_installer_skips_checksum_when_unset(tmp_path: Path) -> None:
    """Without a configured checksum the download does not verify."""
    result = _run_download_installer(tmp_path, cli_version="latest")

    assert result.returncode == 0
    outputs = _read_outputs(tmp_path / "github-output")
    assert outputs["version"] == "latest"
    assert outputs["major_minor"] == "latest"


def test_export_env_writes_token_and_checksum(tmp_path: Path) -> None:
    """The export step surfaces the token and checksum to GITHUB_ENV."""
    script = str(_step_by_name("Export env for later steps")["run"])
    script = script.replace("${{ inputs.access-token }}", "secret-token")
    script = script.replace("${{ inputs.installer-checksum }}", "deadbeef")
    script = script.replace("${{ inputs.project-url }}", "")
    env_file = tmp_path / "github-env"

    result = _run_fragment(tmp_path, script, env={"GITHUB_ENV": str(env_file)})

    assert result.returncode == 0
    content = env_file.read_text(encoding="utf-8")
    assert "CS_ACCESS_TOKEN=secret-token" in content
    assert "CODESCENE_CLI_SHA256=deadbeef" in content
    assert "CS_PROJECT_URL" not in content
