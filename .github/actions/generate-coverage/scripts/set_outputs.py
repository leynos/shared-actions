#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "plumbum>=1.8,<2.0",
# ]
# ///
"""Write coverage metadata outputs for downstream workflow steps."""

import os
import re
import typing as typ
from dataclasses import Field, dataclass, field
from pathlib import Path

import cyclopts
from cmd_utils_loader import run_cmd
from cyclopts import App
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

UNKNOWN_OS: typ.Final[str] = "unknown-os"
UNKNOWN_ARCH: typ.Final[str] = "unknown-arch"
EXTRA_SUFFIX_FALLBACK: typ.Final[str] = "custom"


@dataclass(frozen=True)
class JobInfo:
    name: str
    index: str


@dataclass(frozen=True)
class PlatformInfo:
    os_segment: str
    arch_segment: str


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
    job_info: JobInfo,
    platform_info: PlatformInfo,
    extra_suffix: str | None = None,
) -> str:
    """Compose the coverage artifact name with os/arch and optional suffix."""
    base = (
        f"{fmt}-{job_info.name}-"
        f"{job_info.index}-{platform_info.os_segment}-{platform_info.arch_segment}"
    )
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


@dataclass
class ArtifactOptions:
    """Artifact naming configuration."""

    job_name: str | None = None
    job_index: str = "0"
    artifact_extra_suffix: str | None = None


@dataclass
class RunnerDetails:
    """Platform detection configuration."""

    runner_os: str | None = None
    runner_arch: str | None = None


_DEFAULT_ARTIFACT_OPTIONS = ArtifactOptions()


@app.default
def main(
    *,
    output_path: Path,
    fmt: str,
    artifact: ArtifactOptions = field(default_factory=ArtifactOptions),
    runner: RunnerDetails = field(default_factory=RunnerDetails),
) -> None:
    """Generate coverage outputs with platform-aware artifact naming."""
    artifact_from_default = False
    if isinstance(artifact, Field):
        artifact = artifact.default_factory()
        artifact_from_default = True
    runner_from_default = False
    if isinstance(runner, Field):
        runner = runner.default_factory()
        runner_from_default = True

    env_job_name = _normalise_optional(os.environ.get("INPUT_JOB_NAME"))
    env_job_index = _normalise_optional(os.environ.get("INPUT_JOB_INDEX"))
    env_extra_suffix = os.environ.get("INPUT_ARTIFACT_EXTRA_SUFFIX")
    env_runner_os = os.environ.get("INPUT_RUNNER_OS")
    env_runner_arch = os.environ.get("INPUT_RUNNER_ARCH")

    artifact_job_name = artifact.job_name
    if artifact_job_name is None:
        artifact_job_name = env_job_name
    elif artifact_from_default and env_job_name is not None:
        artifact_job_name = env_job_name

    artifact_job_index = artifact.job_index
    if artifact_from_default and env_job_index is not None:
        artifact_job_index = env_job_index
    elif not artifact_job_index:
        artifact_job_index = env_job_index or _DEFAULT_ARTIFACT_OPTIONS.job_index
    if artifact_job_index is None:
        artifact_job_index = _DEFAULT_ARTIFACT_OPTIONS.job_index

    artifact_extra_suffix = artifact.artifact_extra_suffix
    if artifact_extra_suffix is None:
        artifact_extra_suffix = env_extra_suffix
    elif artifact_from_default and env_extra_suffix is not None:
        artifact_extra_suffix = env_extra_suffix

    resolved_job_name = artifact_job_name or os.environ.get("GITHUB_JOB", "job")

    runner_os = runner.runner_os
    if runner_os is None or runner_from_default:
        runner_os = env_runner_os if env_runner_os is not None else runner_os

    runner_arch = runner.runner_arch
    if runner_arch is None or runner_from_default:
        runner_arch = env_runner_arch if env_runner_arch is not None else runner_arch

    os_segment, arch_segment = detect_runner_details(runner_os, runner_arch)
    job_info = JobInfo(name=resolved_job_name, index=artifact_job_index)
    platform_info = PlatformInfo(os_segment=os_segment, arch_segment=arch_segment)
    artifact_name = build_artifact_name(
        fmt, job_info, platform_info, artifact_extra_suffix
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
    "ArtifactOptions",
    "build_artifact_name",
    "detect_runner_details",
    "JobInfo",
    "PlatformInfo",
    "RunnerDetails",
    "write_outputs",
]
