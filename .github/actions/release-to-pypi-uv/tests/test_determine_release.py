"""Tests for determine_release.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared_actions_conftest import REQUIRES_UV

pytestmark = REQUIRES_UV


def run_script(
    script: Path, *, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Execute ``determine_release`` with a controlled environment."""
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
    """Construct the base environment shared by the release script tests."""
    merged = {**os.environ}
    root = str(Path(__file__).resolve().parents[4])
    prev = os.environ.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    merged["PYTHONIOENCODING"] = "utf-8"
    merged["GITHUB_OUTPUT"] = str(tmp_path / "out.txt")
    merged["PWD"] = str(tmp_path)
    return merged


def read_outputs(tmp_path: Path) -> dict[str, str]:
    """Return ``GITHUB_OUTPUT`` key/value pairs emitted by the script."""
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
    """Derive the release tag from Git reference metadata."""
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
    """Derive the release tag from the workflow input when present."""
    env = base_env(tmp_path)
    env["INPUT_TAG"] = "v2.0.0"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 0, result.stderr
    outputs = read_outputs(tmp_path)
    assert outputs["tag"] == "v2.0.0"
    assert outputs["version"] == "2.0.0"


def test_rejects_invalid_tag(tmp_path: Path) -> None:
    """Reject release tags that do not follow the expected SemVer format."""
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = "release-1.0.0"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "Tag must be a valid semantic version" in result.stderr


def test_errors_when_no_tag_and_not_on_tag_ref(tmp_path: Path) -> None:
    """Fail when neither Git metadata nor inputs provide a tag."""
    env = base_env(tmp_path)
    env.pop("GITHUB_REF_TYPE", None)
    env.pop("GITHUB_REF_NAME", None)
    env.pop("INPUT_TAG", None)

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_type_missing(tmp_path: Path) -> None:
    """Fail when ``GITHUB_REF_TYPE`` is absent."""
    env = base_env(tmp_path)
    env.pop("GITHUB_REF_TYPE", None)
    env["GITHUB_REF_NAME"] = "v1.2.3"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_name_missing(tmp_path: Path) -> None:
    """Fail when ``GITHUB_REF_NAME`` is not provided."""
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env.pop("GITHUB_REF_NAME", None)

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_when_ref_name_empty(tmp_path: Path) -> None:
    """Fail when ``GITHUB_REF_NAME`` is empty."""
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = ""

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "No tag was provided" in result.stderr


def test_errors_on_malformed_version_tag(tmp_path: Path) -> None:
    """Fail when the release tag omits version components."""
    env = base_env(tmp_path)
    env["GITHUB_REF_TYPE"] = "tag"
    env["GITHUB_REF_NAME"] = "v1.2"

    script = Path(__file__).resolve().parents[1] / "scripts" / "determine_release.py"
    result = run_script(script, env=env)

    assert result.returncode == 1
    assert "Tag must be a valid semantic version" in result.stderr
