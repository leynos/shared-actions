"""Behavioural contracts for the CodeScene coverage upload step.

The step's own ``run:`` body is executed in bash with a stand-in
``cs-coverage`` on ``PATH``, bound through the same environment variables the
composite action sets, so each test exercises the shipped script rather than
a copy of it. Extracted from #408.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"
#: The step under test, by its name in ``action.yml``.
UPLOAD_STEP = "Upload coverage to CodeScene"


def _upload_script() -> str:
    """Return the upload step's ``run:`` body from the action manifest."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    step = next(
        step for step in manifest["runs"]["steps"] if step.get("name") == UPLOAD_STEP
    )
    return str(step["run"])


def _write_stub_cli(directory: Path) -> None:
    """Write a ``cs-coverage`` stand-in that echoes its arguments."""
    cli = directory / "cs-coverage"
    cli.write_text(
        "#!/usr/bin/env bash\nprintf 'arguments: %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)


def _run_upload(
    tmp_path: Path, *, search_path: str, coverage_file: str = "coverage.xml"
) -> subprocess.CompletedProcess[str]:
    """Run the upload step in *tmp_path* with *search_path* as ``PATH``.

    GitHub runs a ``bash`` step with ``-e -o pipefail``, so the flags are
    mirrored here.
    """
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-e", "-o", "pipefail", "-c", _upload_script()],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": search_path,
            "COVERAGE_FILE": coverage_file,
            "INPUT_FORMAT": "cobertura",
            "CS_ACCESS_TOKEN": "token",
        },
        text=True,
    )


def _stub_path(tmp_path: Path) -> str:
    """Return a ``PATH`` that finds the stand-in CLI before anything else."""
    return f"{tmp_path}{os.pathsep}{os.environ['PATH']}"


def test_upload_sends_the_report_as_line_coverage(tmp_path: Path) -> None:
    """A present report is uploaded in its format as the line-coverage metric."""
    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    _write_stub_cli(tmp_path)

    result = _run_upload(tmp_path, search_path=_stub_path(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "arguments: upload --format cobertura --metric line-coverage coverage.xml"
    ]


def test_upload_refuses_a_missing_report_and_names_it(tmp_path: Path) -> None:
    """A missing report stops the upload before the CLI is called."""
    _write_stub_cli(tmp_path)

    result = _run_upload(
        tmp_path, search_path=_stub_path(tmp_path), coverage_file="absent.xml"
    )

    assert result.returncode == 1
    assert "Coverage file not found: absent.xml" in result.stderr
    assert "arguments:" not in result.stdout


def test_upload_refuses_when_the_cli_is_not_installed(tmp_path: Path) -> None:
    """Without ``cs-coverage`` on ``PATH`` the step fails and says why.

    ``PATH`` holds only an empty directory plus the directory of ``bash``
    itself, so no ``cs-coverage`` elsewhere on the host can be found.
    """
    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    empty = tmp_path / "bin"
    empty.mkdir()
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    search_path = f"{empty}{os.pathsep}{Path(bash).parent}"
    if shutil.which("cs-coverage", path=search_path) is not None:
        pytest.skip("cs-coverage is installed beside bash on this host")

    result = _run_upload(tmp_path, search_path=search_path)

    assert result.returncode == 1
    assert "cs-coverage CLI not found" in result.stderr
