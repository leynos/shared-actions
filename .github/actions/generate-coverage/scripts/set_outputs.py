#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=3.24,<4.0", "plumbum>=1.8,<2.0", "typer"]
# ///
"""Write coverage output metadata for the caller workflow."""

from __future__ import annotations

import os
import re
import sys
import typing as typ
from pathlib import Path

import cyclopts
from cmd_utils_loader import run_cmd
from cyclopts import App, Parameter
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
if not isinstance(existing_config, tuple):
    existing_config = tuple(existing_config)
app.config = (*existing_config, _env_config)


def _normalise_component(value: str | None, fallback: str) -> str:
    """Return *value* sanitised for use in artefact names."""
    raw = (value or fallback).strip()
    if not raw:
        raw = fallback
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return cleaned.lower() or fallback


def _detect_runner_labels(
    default_os: str | None,
    default_arch: str | None,
) -> tuple[str, str]:
    """Return normalised platform identifiers for the current runner."""
    script = "import platform;print(platform.system());print(platform.machine())"
    command = local[sys.executable]["-c", script]
    try:
        output = run_cmd(command)
    except ProcessExecutionError:
        system = default_os or "unknown-os"
        arch = default_arch or "unknown-arch"
    else:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if len(lines) >= 2:
            system, arch = lines[0], lines[1]
        else:
            system = default_os or "unknown-os"
            arch = default_arch or "unknown-arch"
    return (
        _normalise_component(system, "unknown-os"),
        _normalise_component(arch, "unknown-arch"),
    )


def build_artifact_name(
    fmt: str,
    job: str,
    job_index: str | None,
    runner_os: str,
    runner_arch: str,
    extra_suffix: str | None = None,
) -> str:
    """Compose the coverage artefact name using workflow metadata."""
    index = job_index.strip() if job_index else ""
    if not index.isdigit():
        index = "0"

    components: list[str] = [
        _normalise_component(fmt, "coverage"),
        _normalise_component(job, "job"),
        _normalise_component(index, "0"),
        _normalise_component(runner_os, "unknown-os"),
        _normalise_component(runner_arch, "unknown-arch"),
    ]

    if extra_suffix:
        components.append(_normalise_component(extra_suffix, "extra"))

    return "-".join(components)


@app.default
def main(
    *,
    output_path: typ.Annotated[Path, Parameter(required=True)],
    fmt: str | None = None,
    artifact_name_suffix: str | None = None,
    github_output: Path | None = None,
) -> None:
    """Write final outputs to ``GITHUB_OUTPUT`` for the caller workflow."""
    fmt_value = fmt or os.environ.get("DETECTED_FMT")
    if not fmt_value:
        raise RuntimeError("Coverage format is required via --fmt or DETECTED_FMT")

    job = os.environ.get("GITHUB_JOB", "job")
    job_index = os.environ.get("STRATEGY_JOB_INDEX", "")
    default_os = os.environ.get("RUNNER_OS")
    default_arch = os.environ.get("RUNNER_ARCH")

    runner_os, runner_arch = _detect_runner_labels(default_os, default_arch)
    artifact_name = build_artifact_name(
        fmt_value,
        job,
        job_index,
        runner_os,
        runner_arch,
        artifact_name_suffix,
    )

    output_file = github_output or Path(os.environ["GITHUB_OUTPUT"])
    with output_file.open("a") as fh:
        fh.write(f"file={output_path}\n")
        fh.write(f"format={fmt_value}\n")
        fh.write(f"artifact_name={artifact_name}\n")


if __name__ == "__main__":
    app()
