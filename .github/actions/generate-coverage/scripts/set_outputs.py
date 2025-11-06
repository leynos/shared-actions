#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "plumbum>=1.8,<2.0",
# ]
# ///
"""Write coverage metadata outputs for downstream workflow steps."""

from __future__ import annotations

import os
import re
import typing as typ
from pathlib import Path

import cyclopts
from cmd_utils_loader import run_cmd
from cyclopts import App
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

UNKNOWN_OS: typ.Final[str] = "unknown-os"
UNKNOWN_ARCH: typ.Final[str] = "unknown-arch"
EXTRA_SUFFIX_FALLBACK: typ.Final[str] = "custom"


_slug_pattern = re.compile(r"[^a-z0-9]+")

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
_existing_config = getattr(app, "config", None)
if _existing_config is None:
    app.config = (_env_config,)
else:
    app.config = (*tuple(_existing_config), _env_config)


def _github_output_path() -> Path:
    try:
        github_output = os.environ["GITHUB_OUTPUT"]
    except KeyError as exc:  # pragma: no cover - contract enforced by GitHub
        message = "GITHUB_OUTPUT environment variable is required"
        raise RuntimeError(message) from exc
    return Path(github_output)


def _normalise_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _slug(value: str, fallback: str) -> str:
    candidate = _slug_pattern.sub("-", value.strip().lower()).strip("-")
    return candidate or fallback


def _run_uname(flag: str) -> str | None:
    try:
        result = run_cmd(local["uname"][flag])
    except (ProcessExecutionError, OSError):  # pragma: no cover - platform guard
        return None
    normalized = str(result).strip()
    return normalized or None


def detect_runner_details(
    runner_os: str | None,
    runner_arch: str | None,
) -> tuple[str, str]:
    """Return normalised (os, arch) strings using env hints or uname fallbacks."""
    os_hint = _normalise_optional(runner_os) or _normalise_optional(
        os.environ.get("RUNNER_OS")
    )
    arch_hint = _normalise_optional(runner_arch) or _normalise_optional(
        os.environ.get("RUNNER_ARCH")
    )

    if os_hint is None:
        os_hint = _run_uname("-s")
    if arch_hint is None:
        arch_hint = _run_uname("-m")

    return (
        _slug(os_hint or UNKNOWN_OS, UNKNOWN_OS),
        _slug(arch_hint or UNKNOWN_ARCH, UNKNOWN_ARCH),
    )


def build_artifact_name(
    fmt: str,
    job_name: str,
    job_index: str,
    os_segment: str,
    arch_segment: str,
    extra_suffix: str | None = None,
) -> str:
    """Compose the coverage artifact name with os/arch and optional suffix."""
    base = f"{fmt}-{job_name}-{job_index}-{os_segment}-{arch_segment}"
    suffix = _normalise_optional(extra_suffix)
    if suffix is None:
        return base
    return f"{base}-{_slug(suffix, EXTRA_SUFFIX_FALLBACK)}"


def write_outputs(
    github_output: Path,
    *,
    output_path: Path,
    fmt: str,
    artifact_name: str,
) -> None:
    """Append ``file``, ``format`` and ``artifact-name`` outputs."""
    lines = (
        f"file={output_path}",
        f"format={fmt}",
        f"artifact-name={artifact_name}",
    )
    with github_output.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")


@app.default
def main(
    *,
    output_path: Path,
    fmt: str,
    job_name: str | None = None,
    job_index: str = "0",
    artifact_extra_suffix: str | None = None,
    runner_os: str | None = None,
    runner_arch: str | None = None,
) -> None:
    resolved_job_name = job_name or os.environ.get("GITHUB_JOB", "job")
    os_segment, arch_segment = detect_runner_details(runner_os, runner_arch)
    artifact_name = build_artifact_name(
        fmt,
        resolved_job_name,
        job_index,
        os_segment,
        arch_segment,
        artifact_extra_suffix,
    )
    write_outputs(
        _github_output_path(),
        output_path=output_path,
        fmt=fmt,
        artifact_name=artifact_name,
    )


if __name__ == "__main__":
    app()


__all__ = [
    "build_artifact_name",
    "detect_runner_details",
    "write_outputs",
]
