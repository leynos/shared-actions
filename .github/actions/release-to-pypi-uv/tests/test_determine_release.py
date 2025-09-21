"""Tests for determine_release.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared_actions_conftest import REQUIRES_UV


pytestmark = REQUIRES_UV


def run_script(script: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute the determine-release script with the provided environment.

    Parameters
    ----------
    script : Path
        Path to the script to execute with ``uv run``.
    env : dict[str, str]
        Environment variables to use when invoking the script.

    Returns
    -------
    subprocess.CompletedProcess[str]
        Result object capturing stdout, stderr, and the return code.
    """
    cmd = ["uv", "run", "--script", str(script)]
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        cwd=env.get("PWD"),
    )


def base_env(tmp_path: Path) -> dict[str, str]:
    """Construct the base environment used by the release script tests.

    Parameters
    ----------
    tmp_path : Path
        Temporary working directory provided by pytest.

    Returns
    -------
    dict[str, str]
        Environment mapping that mimics the workflow runtime configuration.
    """
    merged = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    prev = os.environ.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    merged["PYTHONIOENCODING"] = "utf-8"
    merged["GITHUB_OUTPUT"] = str(tmp_path / "out.txt")
    merged["PWD"] = str(tmp_path)
    return merged


def read_outputs(tmp_path: Path) -> dict[str, str]:
    """Read ``GITHUB_OUTPUT`` key/value pairs written by the script.

    Parameters
    ----------
    tmp_path : Path
        Temporary working directory containing the output file.

    Returns
    -------
    dict[str, str]
        Parsed mapping of output names to their recorded values.
    """
    out = {}
    output_file = tmp_path / "out.txt"
    if not output_file.exists():
        return out
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    return out


def test_resolves_tag_from_ref(tmp_path: Path) -> None:
    """Resolve the release tag from the Git reference metadata.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = "v1.2.3"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 0, result.stderr
    outputs = read_outputs(tmp_path)
    assert outputs["tag"] == "v1.2.3"
    assert outputs["version"] == "1.2.3"


def test_resolves_tag_from_input(tmp_path: Path) -> None:
    """Resolve the release tag from the workflow input when provided.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["INPUT_TAG"] = "v2.0.0"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 0, result.stderr
    outputs = read_outputs(tmp_path)
    assert outputs["tag"] == "v2.0.0"
    assert outputs["version"] == "2.0.0"


def test_rejects_invalid_tag(tmp_path: Path) -> None:
    """Reject release tags that do not follow the expected SemVer format.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = "release-1.0.0"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "Tag must be a valid semantic version" in result.stderr


def test_errors_when_no_tag_and_not_on_tag_ref(tmp_path: Path) -> None:
    """Exit with an error when no release tag can be resolved.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env.pop("GITHUB_REF_TYPE", None)
    env.pop("GITHUB_REF_NAME", None)
    env.pop("INPUT_TAG", None)

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_type_missing(tmp_path: Path) -> None:
    """Exit with an error when ``GITHUB_REF_TYPE`` is missing.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env.pop("GITHUB_REF_TYPE", None)
    env["GITHUB_REF_NAME"] = "v1.2.3"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_name_missing(tmp_path: Path) -> None:
    """Exit with an error when ``GITHUB_REF_NAME`` is missing.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env.pop("GITHUB_REF_NAME", None)

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_name_empty(tmp_path: Path) -> None:
    """Exit with an error when ``GITHUB_REF_NAME`` is empty.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = ""

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_on_malformed_version_tag(tmp_path: Path) -> None:
    """Exit with an error when the tag omits components of the version.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    """
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = "v1.2"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "Tag must be a valid semantic version" in result.stderr
