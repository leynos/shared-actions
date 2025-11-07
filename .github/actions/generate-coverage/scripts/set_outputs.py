#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=3.24,<4.0"]
# ///
"""Write coverage output metadata for the caller workflow."""

from __future__ import annotations

import dataclasses as dc
import os
import platform
import re
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

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


def _get_fallback_value(default: str | None, fallback: str) -> str:
    """Return ``default`` when provided, otherwise ``fallback``."""
    if default is None:
        return fallback
    stripped = default.strip()
    return stripped or fallback


def _detect_runner_labels(
    default_os: str | None,
    default_arch: str | None,
) -> tuple[str, str]:
    """Return normalised platform identifiers for the current runner."""
    fallback_system = _get_fallback_value(default_os, "unknown-os")
    fallback_arch = _get_fallback_value(default_arch, "unknown-arch")

    try:
        detected_system = platform.system()
        detected_arch = platform.machine()
    except OSError:
        detected_system = None
        detected_arch = None

    system = _get_fallback_value(detected_system, fallback_system)
    arch = _get_fallback_value(detected_arch, fallback_arch)

    return (
        _normalise_component(system, "unknown-os"),
        _normalise_component(arch, "unknown-arch"),
    )


def _resolve_github_output_path(github_output: Path | None) -> Path:
    """Return the writable path for ``GITHUB_OUTPUT``."""
    if github_output is not None:
        return github_output
    env_value = os.environ.get("GITHUB_OUTPUT")
    if env_value and env_value.strip():
        return Path(env_value)
    message = (
        "GITHUB_OUTPUT is not set; provide --github-output or export the "
        "environment variable before invoking set_outputs.py"
    )
    raise RuntimeError(message)


@dc.dataclass(slots=True)
class ArtefactNameComponents:
    """Input fields used to construct the coverage artefact name."""

    fmt: str
    job: str
    job_index: str | None
    runner_os: str
    runner_arch: str
    extra_suffix: str | None = None


def build_artefact_name(components: ArtefactNameComponents) -> str:
    """Compose the coverage artefact name using workflow metadata."""
    index = components.job_index.strip() if components.job_index else ""
    if not index.isdigit():
        index = "0"

    parts: list[str] = [
        _normalise_component(components.fmt, "coverage"),
        _normalise_component(components.job, "job"),
        _normalise_component(index, "0"),
        _normalise_component(components.runner_os, "unknown-os"),
        _normalise_component(components.runner_arch, "unknown-arch"),
    ]

    if components.extra_suffix:
        parts.append(_normalise_component(components.extra_suffix, "extra"))

    return "-".join(parts)


@app.default
def main(
    *,
    output_path: typ.Annotated[Path, Parameter(required=True)],
    fmt: str | None = None,
    artefact_name_suffix: str | None = None,
    github_output: Path | None = None,
) -> None:
    """Write final outputs to ``GITHUB_OUTPUT`` for the caller workflow."""
    fmt_value = fmt or os.environ.get("DETECTED_FMT")
    if not fmt_value:
        message = "Coverage format is required via --fmt or DETECTED_FMT"
        raise RuntimeError(message)

    job = os.environ.get("GITHUB_JOB", "job")
    job_index = os.environ.get("STRATEGY_JOB_INDEX", "")
    default_os = os.environ.get("RUNNER_OS")
    default_arch = os.environ.get("RUNNER_ARCH")

    runner_os, runner_arch = _detect_runner_labels(default_os, default_arch)
    artefact_name = build_artefact_name(
        ArtefactNameComponents(
            fmt=fmt_value,
            job=job,
            job_index=job_index,
            runner_os=runner_os,
            runner_arch=runner_arch,
            extra_suffix=artefact_name_suffix,
        )
    )

    output_file = _resolve_github_output_path(github_output)
    with output_file.open("a") as fh:
        fh.write(f"file={output_path}\n")
        fh.write(f"format={fmt_value}\n")
        fh.write(f"artefact_name={artefact_name}\n")


if __name__ == "__main__":
    app()
