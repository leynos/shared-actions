"""Shared helpers for the `resolve`/`install` CLI tests."""

from __future__ import annotations

from pathlib import Path


def fake_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment mapping with fresh, empty output files."""
    output_path = tmp_path / "github_output.txt"
    summary_path = tmp_path / "github_step_summary.txt"
    output_path.write_text("")
    summary_path.write_text("")
    return {
        "GITHUB_OUTPUT": str(output_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }


def outputs(env: dict[str, str]) -> dict[str, str]:
    """Parse the `key=value` lines `GITHUB_OUTPUT` accumulated."""
    lines = Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line)
