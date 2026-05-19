"""Core artefact staging pipeline for release artefact workflows.

This module turns a loaded :class:`stage_common.config.StagingConfig` into a
domain result describing staged files, named outputs, checksum digests, skipped
optional artefacts, and optional PowerShell sidecar metadata. It is deliberately
independent from GitHub Actions output-file formatting; the CLI layer in
``stage.py`` serializes :class:`StageResult` through ``stage_common.output``.

Key relationships:
- ``stage_common.config`` owns TOML parsing and target-specific configuration.
- ``stage_common.resolution`` resolves source templates, direct paths, and
  globs against the configured workspace.
- ``stage_common.output`` formats and writes workflow outputs outside this
  pipeline so staging logic can be tested without GitHub Actions files.

Example usage::

    config = load_config(config_path, "linux-x86_64", workspace=workspace)
    result = stage_artefacts(config, ps_module_name="MyTool")
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import shutil
import time
import typing as typ
import uuid

from .errors import StageError

if typ.TYPE_CHECKING:
    from pathlib import Path

from .output import validate_no_reserved_key_collisions
from .resolution import match_candidate_path

if typ.TYPE_CHECKING:
    from .config import ArtefactConfig, StagingConfig

__all__ = ["StageResult", "stage_artefacts"]


logger = logging.getLogger(__name__)

_CORR_ID_CONTEXT_KEY = "__corr_id"


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
    powershell_help_dir: Path | None = None
    skipped_artefacts: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(slots=True, frozen=True)
class StageEnv:
    config: StagingConfig
    staging_dir: Path
    context: dict[str, typ.Any]


@dataclasses.dataclass(slots=True, frozen=True)
class StagingDirs:
    """Bundle workspace and staging directory paths."""

    workspace: Path
    staging_dir: Path


@dataclasses.dataclass(slots=True, frozen=True)
class StagedArtefact:
    """Describe a staged artefact yielded by :func:`_iter_staged_artefacts`."""

    path: Path
    artefact: ArtefactConfig
    checksum: str


@dataclasses.dataclass(slots=True, frozen=True)
class ResolvedArtefact:
    artefact: ArtefactConfig
    source: Path
    destination: Path


@dataclasses.dataclass(slots=True)
class StagingState:
    staged_paths: list[Path]
    outputs: dict[str, Path]
    checksums: dict[str, str]
    skipped_artefacts: list[str]


def _corr_id(env: StageEnv) -> str:
    """Return the staging correlation identifier carried in ``env``."""
    return typ.cast("str", env.context.get(_CORR_ID_CONTEXT_KEY, ""))


def _validate_staging_dir_safety(staging_dir: Path, workspace: Path) -> None:
    """Ensure staging_dir is safe to delete by verifying it's under workspace."""
    resolved_staging = staging_dir.resolve()
    resolved_workspace = workspace.resolve()
    if not resolved_staging.is_relative_to(resolved_workspace):
        msg = (
            f"Staging directory must be under workspace. "
            f"staging_dir={resolved_staging}, workspace={resolved_workspace}"
        )
        raise StageError(msg)


def _initialize_staging_dir(staging_dir: Path, workspace: Path) -> None:
    """Create a clean staging directory ready to receive artefacts."""
    _validate_staging_dir_safety(staging_dir, workspace)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)


def _collect_artefacts(env: StageEnv) -> StagingState:
    """Stage configured artefacts and return paths, outputs, checksums, skips.

    Raises
    ------
    StageError
        Raised when no artefacts are staged.
    """
    state = StagingState([], {}, {}, [])

    for artefact in env.config.artefacts:
        _stage_configured_artefact(env, artefact, state)

    if not state.staged_paths:
        artefact_count = len(env.config.artefacts)
        msg = (
            f"No artefacts were staged. "
            f"Configuration defined {artefact_count} artefact(s), "
            f"but none were found or all were optional and missing."
        )
        raise StageError(msg)

    return state


def stage_artefacts(
    config: StagingConfig,
    *,
    ps_module_name: str = "",
    corr_id: str | None = None,
) -> StageResult:
    """Copy artefacts into ``config``'s staging directory.

    Parameters
    ----------
    config
        Fully resolved configuration describing the artefacts to stage.
    ps_module_name
        Optional staged PowerShell module directory name.

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
    corr_id = corr_id or uuid.uuid4().hex
    started_at = time.perf_counter()
    staging_dir = config.staging_dir()
    context = config.as_template_context() | {_CORR_ID_CONTEXT_KEY: corr_id}
    env = StageEnv(config, staging_dir, context)
    logger.info(
        "corr_id=%s Starting artefact staging: target=%s artefact_count=%d "
        "staging_dir=%s ps_module_name=%s",
        corr_id,
        config.target,
        len(config.artefacts),
        staging_dir,
        ps_module_name,
    )

    _initialize_staging_dir(staging_dir, config.workspace)

    state = _collect_artefacts(env)

    validate_no_reserved_key_collisions(state.outputs)
    powershell_help_dir = _resolve_powershell_help_dir(
        staging_dir, state.staged_paths, ps_module_name, corr_id=corr_id
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "corr_id=%s Completed artefact staging: target=%s staged_count=%d "
        "skipped_count=%d checksum_count=%d output_count=%d "
        "powershell_help_dir=%s elapsed_ms=%.2f",
        corr_id,
        config.target,
        len(state.staged_paths),
        len(state.skipped_artefacts),
        len(state.checksums),
        len(state.outputs),
        powershell_help_dir.as_posix() if powershell_help_dir else "",
        elapsed_ms,
    )

    return StageResult(
        staging_dir,
        state.staged_paths,
        state.outputs,
        state.checksums,
        powershell_help_dir,
        state.skipped_artefacts,
    )


def _is_disallowed_ps_module_name(
    staging_root: Path, ps_module_name: str, module_dir: Path
) -> bool:
    """Return True when ``ps_module_name`` cannot name a direct module child."""
    return (
        ps_module_name in {".", ".."}
        or "/" in ps_module_name
        or "\\" in ps_module_name
        or module_dir.parent != staging_root
    )


def _resolve_powershell_help_dir(
    staging_dir: Path,
    staged_paths: list[Path],
    ps_module_name: str,
    *,
    corr_id: str = "",
) -> Path | None:
    """Return the staged PowerShell module directory when explicitly named."""
    if not ps_module_name:
        logger.info(
            "corr_id=%s PowerShell help directory not resolved: module name is empty.",
            corr_id,
        )
        return None

    staging_root = staging_dir.resolve()
    module_dir = (staging_dir / ps_module_name).resolve()
    logger.debug(
        "corr_id=%s PowerShell module resolution check: module_dir=%s exists=%s "
        "is_dir=%s",
        corr_id,
        module_dir,
        module_dir.exists(),
        module_dir.is_dir(),
    )
    if _is_disallowed_ps_module_name(staging_root, ps_module_name, module_dir):
        logger.info(
            "corr_id=%s PowerShell help directory not resolved: "
            "invalid module name %r.",
            corr_id,
            ps_module_name,
        )
        return None

    if module_dir.is_dir() and any(
        staged_path.resolve().is_relative_to(module_dir) for staged_path in staged_paths
    ):
        logger.info(
            "corr_id=%s PowerShell help directory resolved to %s.",
            corr_id,
            module_dir,
        )
        return module_dir
    logger.info(
        "corr_id=%s PowerShell help directory not resolved: no staged files under %s.",
        corr_id,
        module_dir,
    )
    return None


def _ensure_source_available(
    env: StageEnv,
    artefact: ArtefactConfig,
    candidate: tuple[Path | None, list[_RenderAttempt]],
) -> bool:
    """Return ``True`` when ``source_path`` exists, otherwise handle the miss."""
    source_path, attempts = candidate
    if source_path is not None:
        return True

    if artefact.required:
        attempt_lines = ", ".join(
            f"{attempt.template!r} -> {attempt.rendered!r}" for attempt in attempts
        )
        msg = (
            "Required artefact not found. "
            f"Workspace={env.config.workspace.as_posix()} "
            f"Attempts=[{attempt_lines}]"
        )
        raise StageError(msg)

    logger.warning(
        "corr_id=%s Optional artefact missing: source=%s workspace=%s attempts=%d",
        _corr_id(env),
        artefact.source,
        env.config.workspace.as_posix(),
        len(attempts),
    )
    return False


def _resolve_configured_artefact(
    env: StageEnv,
    artefact: ArtefactConfig,
) -> typ.Iterator[ResolvedArtefact]:
    """Resolve source and destination paths for one configured artefact."""
    candidate = _resolve_artefact_source(env.config.workspace, artefact, env.context)
    if not _ensure_source_available(env, artefact, candidate):
        return

    source_path, _ = candidate
    source_path = typ.cast("Path", source_path)
    destination_path = _resolve_destination_path(
        env.staging_dir,
        env.context,
        artefact.destination,
        source_path,
    )
    yield ResolvedArtefact(artefact, source_path, destination_path)


def _iter_resolved_artefacts(env: StageEnv) -> typ.Iterator[ResolvedArtefact]:
    """Yield artefacts with resolved source and destination paths."""
    for artefact in env.config.artefacts:
        yield from _resolve_configured_artefact(env, artefact)


def _stage_resolved_artefact(env: StageEnv, ra: ResolvedArtefact) -> tuple[Path, str]:
    """Copy a resolved artefact and write its checksum sidecar."""
    _copy_resolved_artefact(
        StagingDirs(env.config.workspace, env.staging_dir),
        ra.source,
        ra.destination,
        _corr_id(env),
    )
    digest = _write_checksum(ra.destination, env.config.checksum_algorithm)
    logger.debug(
        "corr_id=%s staged %s -> %s checksum=%s",
        _corr_id(env),
        ra.source,
        ra.destination,
        digest,
    )
    return ra.destination, digest


def _stage_configured_artefact(
    env: StageEnv, artefact: ArtefactConfig, state: StagingState
) -> None:
    """Stage one configured artefact and update ``state``."""
    resolved = next(_resolve_configured_artefact(env, artefact), None)
    if resolved is None:
        state.skipped_artefacts.append(artefact.source)
        return
    path, digest = _stage_resolved_artefact(env, resolved)
    state.staged_paths.append(path)
    relative_path = path.relative_to(env.staging_dir).as_posix()
    state.checksums[relative_path] = digest
    if resolved.artefact.output:
        state.outputs[resolved.artefact.output] = path


def _iter_staged_artefacts(
    config: StagingConfig, staging_dir: Path, context: dict[str, typ.Any]
) -> typ.Iterator[StagedArtefact]:
    """Yield :class:`StagedArtefact` entries describing staged artefacts."""
    # TODO(#269): remove this compatibility wrapper after callers migrate to
    # _iter_resolved_artefacts or stage_artefacts.
    env = StageEnv(config, staging_dir, context)
    for resolved in _iter_resolved_artefacts(env):
        path, digest = _stage_resolved_artefact(env, resolved)
        yield StagedArtefact(path, resolved.artefact, digest)


def _resolve_destination_path(
    staging_dir: Path,
    context: dict[str, typ.Any],
    artefact_destination: str | None,
    source_path: Path,
) -> Path:
    """Resolve the staged destination path for ``source_path``."""
    artefact_context = context | {
        "source_path": source_path.as_posix(),
        "source_name": source_path.name,
    }
    destination_text = (
        _render_template(destination, artefact_context)
        if (destination := artefact_destination)
        else source_path.name
    )

    return _safe_destination_path(staging_dir, destination_text)


def _copy_resolved_artefact(
    dirs: StagingDirs,
    source_path: Path,
    destination_path: Path,
    corr_id: str,
) -> None:
    """Copy ``source_path`` to a resolved staged destination path."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        logger.info(
            "corr_id=%s Overwriting existing file: %s", corr_id, destination_path
        )
        destination_path.unlink()
    shutil.copy2(source_path, destination_path)
    logger.info(
        "corr_id=%s Staged '%s' -> '%s'",
        corr_id,
        source_path.relative_to(dirs.workspace),
        destination_path.relative_to(dirs.workspace),
    )


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
        msg = (
            f"Destination escapes staging directory: {destination!r} "
            f"(resolved to {target}, staging_root={staging_root})"
        )
        raise StageError(msg)
    return target


def _write_checksum(path: Path, algorithm: str) -> str:
    r"""Write the checksum sidecar for ``path`` and return the digest.

    The sidecar file uses the BSD-style checksum format compatible with
    standard tools like ``sha256sum -c``: ``<digest>  <filename>\n``
    (two spaces between digest and filename).
    """
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    checksum_path = path.with_name(f"{path.name}.{algorithm}")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest
