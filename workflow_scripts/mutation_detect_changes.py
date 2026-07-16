#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "plumbum>=1.8,<3.0"]
# ///

"""Detect recently changed source files and emit a mutation-run matrix.

This script implements the change-detection guard for the mutation-testing
reusable workflows. It inspects commits reachable from a base reference
within a time window (commit timestamps, not reflog — safe in fresh CI
clones), buckets the changed files into mutation targets, and emits GitHub
Actions step outputs describing what to run.

Manual ``workflow_dispatch`` runs bypass the guard entirely and produce a
full (unscoped) run, fanned out across ``shard-count`` shards for the root
target. Scoped runs stay single-shard: each shard re-pays the baseline
build-and-test cost, which is not worth it for a handful of mutants.

Environment Variables
---------------------
INPUT_EVENT_NAME : str
    The triggering event name; ``workflow_dispatch`` bypasses detection.
INPUT_WINDOW_HOURS : int, optional
    Detection window in hours. Default: ``25`` (one hour wider than a
    daily cadence, absorbing GitHub cron start-time drift).
INPUT_PATHS : str, optional
    Comma-separated path prefixes belonging to the root target.
    Default: ``src/,examples/,benches/``.
INPUT_EXTRA_CRATE_DIRS : str, optional
    Comma-separated directories of additional crates outside the root
    workspace (files under ``<dir>/src/`` map to that target).
INPUT_PATHSPEC : str, optional
    Git pathspec filtering candidate files. Default: ``*.rs``.
INPUT_SHARD_COUNT : int, optional
    Number of shards for full (dispatch) runs of the root target.
    Default: ``6``.
INPUT_BASE_REF : str, optional
    Reference whose history is inspected. Default: ``origin/main``.
GITHUB_OUTPUT : str
    Path of the step-outputs file.
GITHUB_STEP_SUMMARY : str, optional
    Path of the job-summary file (skip message destination).

Outputs
-------
``has_changes``
    ``true`` or ``false``.
``matrix``
    JSON object ``{"include": [...]}`` for a matrix strategy. Each entry
    carries ``dir`` (crate directory, ``.`` for the root), ``slug``
    (artefact-safe target name), ``files`` (space-separated paths relative
    to ``dir``; empty for a full run), ``shard`` and ``shard_count``.
``root_files``
    Space-separated changed files for the root target (repo-relative) —
    consumed by single-job workflows that do not use the matrix.

Usage
-----
As a workflow step::

    - id: detect
      run: uv run --script workflow_scripts/mutation_detect_changes.py
      env:
        INPUT_EVENT_NAME: ${{ github.event_name }}
"""

from __future__ import annotations

import dataclasses
import json
import os
import typing as typ
from pathlib import Path, PurePosixPath

from cyclopts import App, Parameter
from plumbum import local

if typ.TYPE_CHECKING:
    import collections.abc as cabc

if __package__:
    from .output import emit, fail
else:
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

app = App()

SKIP_SUMMARY_TEMPLATE = """## Mutation testing skipped

No matching source changes on `{base_ref}` in the last {window_hours} hours.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Configuration for change detection and matrix construction.

    Attributes
    ----------
    window_hours : int
        Detection window in hours.
    paths : tuple[str, ...]
        Path prefixes belonging to the root target.
    extra_crate_dirs : tuple[str, ...]
        Directories of additional crates outside the root workspace.
    pathspec : str
        Git pathspec filtering candidate files.
    shard_count : int
        Shard count for full runs of the root target.
    base_ref : str
        Reference whose history is inspected.
    """

    window_hours: int = 25
    paths: tuple[str, ...] = ("src/", "examples/", "benches/")
    extra_crate_dirs: tuple[str, ...] = ()
    pathspec: str = "*.rs"
    shard_count: int = 6
    base_ref: str = "origin/main"


@dataclasses.dataclass(frozen=True, slots=True)
class MatrixEntry:
    """One mutation-run target within the workflow matrix.

    Attributes
    ----------
    dir : str
        Crate directory the run executes in (``.`` for the root target).
    slug : str
        Artefact-safe target name.
    files : str
        Space-separated file paths relative to ``dir``; empty for a full
        (unscoped) run.
    shard : int
        Zero-based shard index.
    shard_count : int
        Total number of shards for this target.
    """

    dir: str
    slug: str
    files: str
    shard: int
    shard_count: int


def split_csv(raw: str) -> tuple[str, ...]:
    """Split a comma-separated input into trimmed, non-empty parts.

    Parameters
    ----------
    raw : str
        Comma-separated input, possibly with surrounding whitespace or
        empty segments (``"a/, ,b/,"``).

    Returns
    -------
    tuple[str, ...]
        The trimmed, non-empty parts in input order.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def changed_files(
    config: DetectionConfig, *, repo_root: Path | None = None
) -> tuple[str, ...]:
    """List files changed within the window that still exist at the tip.

    Uses ``git log --since`` with commit timestamps rather than reflog
    syntax, which is unavailable in fresh CI clones. Files deleted or
    renamed since the change are filtered out.

    Parameters
    ----------
    config : DetectionConfig
        Detection settings (window, base ref, pathspec).
    repo_root : Path or None, optional
        Repository root; defaults to the current working directory.

    Returns
    -------
    tuple[str, ...]
        Sorted, de-duplicated repo-relative paths.
    """
    root = repo_root if repo_root is not None else Path.cwd()
    git = local["git"]["-C", str(root)]
    raw = git(
        "log",
        f"--since={config.window_hours} hours ago",
        "--name-only",
        "--format=",
        config.base_ref,
        "--",
        config.pathspec,
    )
    names = sorted({line.strip() for line in raw.splitlines() if line.strip()})
    return tuple(name for name in names if (root / name).is_file())


def _is_under(name: str, base: str) -> bool:
    """Return True when path ``name`` sits strictly under directory ``base``."""
    path = PurePosixPath(name)
    base_path = PurePosixPath(base.rstrip("/"))
    return path != base_path and path.is_relative_to(base_path)


def bucket_files(
    files: cabc.Iterable[str], config: DetectionConfig
) -> dict[str, list[str]]:
    """Group changed files by mutation target.

    Parameters
    ----------
    files : Iterable[str]
        Repo-relative changed file paths.
    config : DetectionConfig
        Detection settings supplying root prefixes and extra crate dirs.

    Returns
    -------
    dict[str, list[str]]
        Mapping of target directory (``.`` for the root) to repo-relative
        files. Extra-crate matches take precedence over root prefixes so a
        crate nested under a root prefix cannot be double-counted.
    """
    buckets: dict[str, list[str]] = {}
    for name in files:
        extra = next(
            (d for d in config.extra_crate_dirs if _is_under(name, f"{d}/src")),
            None,
        )
        if extra is not None:
            buckets.setdefault(extra, []).append(name)
        elif any(_is_under(name, prefix) for prefix in config.paths):
            buckets.setdefault(".", []).append(name)
    return buckets


def _slug_for(target_dir: str) -> str:
    """Return an artefact-safe slug for a target directory."""
    return "root" if target_dir == "." else target_dir.replace("/", "-")


def _relative_files(target_dir: str, files: list[str]) -> str:
    """Return ``files`` relative to ``target_dir`` as one space-joined string."""
    if target_dir == ".":
        return " ".join(files)
    base = PurePosixPath(target_dir)
    return " ".join(PurePosixPath(name).relative_to(base).as_posix() for name in files)


def full_run_matrix(config: DetectionConfig) -> list[MatrixEntry]:
    """Build the matrix for a full (dispatch) run.

    The root target fans out across ``shard_count`` shards; extra crates
    are assumed small and run as a single shard each.
    """
    entries = [
        MatrixEntry(
            dir=".",
            slug="root",
            files="",
            shard=shard,
            shard_count=config.shard_count,
        )
        for shard in range(config.shard_count)
    ]
    entries.extend(
        MatrixEntry(dir=d, slug=_slug_for(d), files="", shard=0, shard_count=1)
        for d in config.extra_crate_dirs
    )
    return entries


def _root_first_key(item: tuple[str, list[str]]) -> tuple[bool, str]:
    """Sort key placing the root (``.``) target first, then alphabetical.

    ``.`` sorts before every directory name, so the root-first order also
    falls out of a plain alphabetical sort; the sort-key variants are
    equivalent and cannot be distinguished by dir-prefixed file paths.
    """
    return (item[0] != ".", item[0])  # pragma: no mutate


def scoped_run_matrix(
    buckets: dict[str, list[str]], config: DetectionConfig
) -> list[MatrixEntry]:
    """Build the single-shard matrix for a scoped (scheduled) run."""
    ordered = sorted(buckets.items(), key=_root_first_key)  # pragma: no mutate
    del config  # scoped runs never shard; kept for signature symmetry
    return [
        MatrixEntry(
            dir=target_dir,
            slug=_slug_for(target_dir),
            files=_relative_files(target_dir, files),
            shard=0,
            shard_count=1,
        )
        for target_dir, files in ordered
    ]


def matrix_json(entries: list[MatrixEntry]) -> str:
    """Serialize matrix entries as the ``matrix`` step output value."""
    return json.dumps(
        {"include": [dataclasses.asdict(entry) for entry in entries]},
        sort_keys=True,
    )


def _write_output(name: str, value: str, output_path: Path) -> None:
    """Append one ``name=value`` line to the step-outputs file."""
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _write_skip_summary(config: DetectionConfig) -> None:
    """Write the skip message to the job summary, when available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    # pragma below: encoding is a locale-independent UTF-8 codec alias.
    with Path(summary_path).open("a", encoding="utf-8") as handle:  # pragma: no mutate
        handle.write(
            SKIP_SUMMARY_TEMPLATE.format(
                base_ref=config.base_ref, window_hours=config.window_hours
            )
        )


@app.default
def main(
    *,
    event_name: typ.Annotated[
        str, Parameter(required=True, env_var="INPUT_EVENT_NAME")
    ],
    window_hours: typ.Annotated[int, Parameter(env_var="INPUT_WINDOW_HOURS")] = 25,
    paths: typ.Annotated[
        str, Parameter(env_var="INPUT_PATHS")
    ] = "src/,examples/,benches/",
    extra_crate_dirs: typ.Annotated[
        str, Parameter(env_var="INPUT_EXTRA_CRATE_DIRS")
    ] = "",
    pathspec: typ.Annotated[str, Parameter(env_var="INPUT_PATHSPEC")] = "*.rs",
    shard_count: typ.Annotated[int, Parameter(env_var="INPUT_SHARD_COUNT")] = 6,
    base_ref: typ.Annotated[str, Parameter(env_var="INPUT_BASE_REF")] = "origin/main",
) -> None:
    """Detect changed files and emit the mutation-run matrix.

    Parameters
    ----------
    event_name : str
        Triggering event; ``workflow_dispatch`` bypasses detection.
    window_hours : int
        Detection window in hours.
    paths : str
        Comma-separated root-target path prefixes.
    extra_crate_dirs : str
        Comma-separated extra crate directories.
    pathspec : str
        Git pathspec for candidate files.
    shard_count : int
        Shard count for full runs.
    base_ref : str
        Reference whose history is inspected.

    Raises
    ------
    SystemExit
        Exits with code 1 when ``GITHUB_OUTPUT`` is not set or inputs are
        invalid.
    """
    output_env = os.environ.get("GITHUB_OUTPUT")
    if not output_env:
        fail("GITHUB_OUTPUT is not set")
    output_path = Path(output_env)
    if window_hours <= 0:
        fail(f"window-hours must be positive, got {window_hours}")
    if shard_count <= 0:
        fail(f"shard-count must be positive, got {shard_count}")

    config = DetectionConfig(
        window_hours=window_hours,
        paths=split_csv(paths),
        extra_crate_dirs=split_csv(extra_crate_dirs),
        pathspec=pathspec,
        shard_count=shard_count,
        base_ref=base_ref,
    )

    if event_name == "workflow_dispatch":
        entries = full_run_matrix(config)
        buckets: dict[str, list[str]] = {}
    else:
        buckets = bucket_files(changed_files(config), config)
        entries = scoped_run_matrix(buckets, config)

    has_changes = bool(entries)
    _write_output("has_changes", "true" if has_changes else "false", output_path)
    _write_output("matrix", matrix_json(entries), output_path)
    _write_output("root_files", " ".join(buckets.get(".", [])), output_path)
    if not has_changes:
        _write_skip_summary(config)
    emit("mutation_detect_has_changes", has_changes)
    emit("mutation_detect_targets", [entry.slug for entry in entries])


if __name__ == "__main__":
    app()
