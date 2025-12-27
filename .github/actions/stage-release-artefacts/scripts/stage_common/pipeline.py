"""Core artefact staging pipeline and supporting helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import shutil
import typing as typ

from .errors import StageError

if typ.TYPE_CHECKING:
    from pathlib import Path

from .output import (
    prepare_output_data,
    validate_no_reserved_key_collisions,
    write_github_output,
)
from .resolution import match_candidate_path

if typ.TYPE_CHECKING:
    from .config import ArtefactConfig, StagingConfig

__all__ = ["StageResult", "stage_artefacts"]


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class _RenderAttempt:
    template: str
    rendered: str


@dataclasses.dataclass(slots=True)
class StageResult:
    """Outcome of :func:`stage_artefacts`."""

    staging_dir: Path
    staged_artefacts: list[Path]
    outputs: dict[str, Path]
    checksums: dict[str, str]


@dataclasses.dataclass(slots=True, frozen=True)
class StagedArtefact:
    """Describe a staged artefact yielded by :func:`_iter_staged_artefacts`."""

    path: Path
    artefact: ArtefactConfig
    checksum: str


def _initialize_staging_dir(staging_dir: Path) -> None:
    """Create a clean staging directory ready to receive artefacts."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)


def stage_artefacts(
    config: StagingConfig,
    github_output_file: Path,
    *,
    normalize_windows_paths: bool = False,
) -> StageResult:
    """Copy artefacts into ``config``'s staging directory.

    Parameters
    ----------
    config
        Fully resolved configuration describing the artefacts to stage.
    github_output_file
        Path to the ``GITHUB_OUTPUT`` file used to export workflow outputs.
    normalize_windows_paths
        When True, convert backslashes to forward slashes in output paths.

    Returns
    -------
    StageResult
        Summary object describing the staging directory, staged artefacts,
        exported outputs, and checksum digests.

    Raises
    ------
    StageError
        Raised when required artefacts are missing or configuration templates
        render invalid destinations.
    """
    staging_dir = config.staging_dir()
    context = config.as_template_context()

    _initialize_staging_dir(staging_dir)

    staged_paths: list[Path] = []
    outputs: dict[str, Path] = {}
    checksums: dict[str, str] = {}

    for staged in _iter_staged_artefacts(config, staging_dir, context):
        staged_paths.append(staged.path)
        checksums[staged.path.name] = staged.checksum

        if staged.artefact.output:
            outputs[staged.artefact.output] = staged.path

    if not staged_paths:
        msg = "No artefacts were staged."
        raise StageError(msg)

    validate_no_reserved_key_collisions(outputs)
    exported_outputs = prepare_output_data(
        staging_dir, staged_paths, outputs, checksums
    )
    write_github_output(
        github_output_file,
        exported_outputs,
        normalize_windows_paths=normalize_windows_paths,
    )

    return StageResult(staging_dir, staged_paths, outputs, checksums)


def _ensure_source_available(
    source_path: Path | None,
    artefact: ArtefactConfig,
    attempts: list[_RenderAttempt],
    workspace: Path,
) -> bool:
    """Return ``True`` when ``source_path`` exists, otherwise handle the miss."""
    if source_path is not None:
        return True

    if artefact.required:
        attempt_lines = ", ".join(
            f"{attempt.template!r} -> {attempt.rendered!r}" for attempt in attempts
        )
        msg = (
            "Required artefact not found. "
            f"Workspace={workspace.as_posix()} "
            f"Attempts=[{attempt_lines}]"
        )
        raise StageError(msg)

    warning = (
        "::warning title=Artefact Skipped::Optional artefact missing: "
        f"{artefact.source}"
    )
    logger.warning(warning)
    return False


def _iter_staged_artefacts(
    config: StagingConfig, staging_dir: Path, context: dict[str, typ.Any]
) -> typ.Iterator[StagedArtefact]:
    """Yield :class:`StagedArtefact` entries describing staged artefacts."""
    for artefact in config.artefacts:
        source_path, attempts = _resolve_artefact_source(
            config.workspace, artefact, context
        )
        if not _ensure_source_available(
            source_path, artefact, attempts, config.workspace
        ):
            continue

        destination_path = _stage_single_artefact(
            config, staging_dir, context, artefact, typ.cast("Path", source_path)
        )
        digest = _write_checksum(destination_path, config.checksum_algorithm)
        yield StagedArtefact(destination_path, artefact, digest)


def _stage_single_artefact(
    config: StagingConfig,
    staging_dir: Path,
    context: dict[str, typ.Any],
    artefact: ArtefactConfig,
    source_path: Path,
) -> Path:
    """Copy ``source_path`` into ``staging_dir`` and return the staged path."""
    artefact_context = context | {
        "source_path": source_path.as_posix(),
        "source_name": source_path.name,
    }
    destination_text = (
        _render_template(destination, artefact_context)
        if (destination := artefact.destination)
        else source_path.name
    )

    destination_path = _safe_destination_path(staging_dir, destination_text)
    if destination_path.exists():
        destination_path.unlink()
    shutil.copy2(source_path, destination_path)
    logger.info(
        "Staged '%s' -> '%s'",
        source_path.relative_to(config.workspace),
        destination_path.relative_to(config.workspace),
    )
    return destination_path


def _render_template(template: str, context: dict[str, typ.Any]) -> str:
    """Render ``template`` with ``context`` and normalise formatting errors."""
    try:
        return template.format(**context)
    except KeyError as exc:
        msg = f"Invalid template key {exc} in '{template}'"
        raise StageError(msg) from exc
    except ValueError as exc:
        msg = f"Template formatting error: {exc} in '{template}'"
        raise StageError(msg) from exc


def _resolve_artefact_source(
    workspace: Path, artefact: ArtefactConfig, context: dict[str, typ.Any]
) -> tuple[Path | None, list[_RenderAttempt]]:
    """Return the first artefact match and attempted renders."""
    attempts: list[_RenderAttempt] = []
    patterns = [artefact.source, *artefact.alternatives]
    for pattern in patterns:
        rendered = _render_template(pattern, context)
        attempts.append(_RenderAttempt(pattern, rendered))
        if (candidate := match_candidate_path(workspace, rendered)) is not None:
            return candidate, attempts
    return None, attempts


def _safe_destination_path(staging_dir: Path, destination: str) -> Path:
    """Resolve ``destination`` under ``staging_dir`` safely."""
    target = (staging_dir / destination).resolve()
    staging_root = staging_dir.resolve()
    if not target.is_relative_to(staging_root):
        msg = f"Destination escapes staging directory: {destination}"
        raise StageError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _write_checksum(path: Path, algorithm: str) -> str:
    """Write the checksum sidecar for ``path`` and return the digest."""
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    checksum_path = path.with_name(f"{path.name}.{algorithm}")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest
