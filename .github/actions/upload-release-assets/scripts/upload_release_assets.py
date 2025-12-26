#!/usr/bin/env -S uv run --script
# fmt: off
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
#   "plumbum>=1.8,<2.0",
# ]
# ///
# fmt: on

"""Upload packaged release artefacts to a GitHub release.

The script discovers artefacts in a staging directory, validates their
filenames and sizes, and optionally uploads them using the GitHub CLI. It is
idempotent and supports a dry-run mode used by the release dry-run workflow to
assert expected asset names without mutating state.

Examples
--------
Upload artefacts to the ``v1.2.3`` release::

    INPUT_RELEASE_TAG=v1.2.3 INPUT_BIN_NAME=myapp uv run upload_release_assets.py

Validate artefacts without uploading (dry-run)::

    INPUT_DRY_RUN=true INPUT_RELEASE_TAG=v1.2.3 INPUT_BIN_NAME=myapp \
        uv run upload_release_assets.py
"""

from __future__ import annotations

import dataclasses as dc
import os
import sys
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from plumbum import local
from plumbum.commands import CommandNotFound, ProcessExecutionError

if typ.TYPE_CHECKING:
    from plumbum.commands.base import BoundCommand


class AssetError(RuntimeError):
    """Raised when the staged artefacts are invalid."""


@dc.dataclass(frozen=True)
class ReleaseAsset:
    """Artefact staged for upload to a GitHub release."""

    path: Path
    asset_name: str
    size: int


app: App = App(config=cyclopts.config.Env("INPUT_", command=False))


def _coerce_bool(value: object, *, default: bool) -> bool:
    """Interpret GitHub input values as booleans.

    GitHub Actions forwards ``workflow_call`` inputs as strings, so we accept a
    variety of spellings. ``None`` or empty strings fall back to ``default``.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().lower()
        if not normalised:
            return default
        if normalised in {"1", "true", "yes", "on"}:
            return True
        if normalised in {"0", "false", "no", "off"}:
            return False
    msg = f"Cannot interpret {value!r} as boolean"
    raise ValueError(msg)


def _is_candidate(path: Path, bin_name: str) -> bool:
    """Return True if the file is a release artefact candidate."""
    name = path.name
    if name in {bin_name, f"{bin_name}.exe", f"{bin_name}.1"}:
        return True
    if name.endswith(".sha256"):
        return True
    return path.suffix in {".deb", ".rpm", ".pkg", ".msi"}


def _resolve_asset_name(path: Path, *, dist_dir: Path) -> str:
    """Return a unique asset name derived from ``path`` within ``dist_dir``."""
    relative_path = path.relative_to(dist_dir)
    if relative_path.parent == Path():
        return relative_path.name
    prefix = relative_path.parent.as_posix().replace("/", "__")
    return f"{prefix}-{relative_path.name}"


def _iter_candidate_paths(dist_dir: Path, bin_name: str) -> typ.Iterator[Path]:
    """Yield candidate artefact paths in sorted order."""
    for path in sorted(dist_dir.rglob("*")):
        if path.is_file() and _is_candidate(path, bin_name):
            yield path


def _require_non_empty(path: Path) -> int:
    """Return the file size, raising AssetError if empty."""
    size = path.stat().st_size
    if size <= 0:
        msg = f"Artefact {path} is empty"
        raise AssetError(msg)
    return size


def _register_asset(asset_name: str, path: Path, seen: dict[str, Path]) -> None:
    """Check for asset name collisions."""
    if previous := seen.get(asset_name):
        msg = (
            f"Asset name collision: "
            f"{asset_name} would upload both {previous} and {path}"
        )
        raise AssetError(msg)
    seen[asset_name] = path


def discover_assets(dist_dir: Path, *, bin_name: str) -> list[ReleaseAsset]:
    """Return the artefacts that should be published.

    Parameters
    ----------
    dist_dir
        Root directory that contains the staged artefacts.
    bin_name
        Binary name used to match platform-specific artefacts.

    Returns
    -------
    list[ReleaseAsset]
        Ordered collection of artefacts ready to upload.

    Raises
    ------
    AssetError
        If no artefacts are found, an artefact is empty, or multiple files would
        upload with the same asset name.

    Examples
    --------
    >>> discover_assets(Path("dist"), bin_name="myapp")  # doctest: +SKIP
    [ReleaseAsset(path=PosixPath('dist/myapp'), ...)]
    """
    if not dist_dir.exists():
        msg = f"Artefact directory {dist_dir} does not exist"
        raise AssetError(msg)

    assets: list[ReleaseAsset] = []
    seen: dict[str, Path] = {}

    for path in _iter_candidate_paths(dist_dir, bin_name):
        size = _require_non_empty(path)
        asset_name = _resolve_asset_name(path, dist_dir=dist_dir)
        _register_asset(asset_name, path, seen)
        assets.append(ReleaseAsset(path=path, asset_name=asset_name, size=size))

    if not assets:
        msg = f"No artefacts discovered in {dist_dir}"
        raise AssetError(msg)

    return assets


def _render_summary(assets: typ.Iterable[ReleaseAsset]) -> str:
    """Return a human-readable upload plan."""
    lines = ["Planned uploads:"]
    lines.extend(
        f"  - {asset.asset_name} ({asset.size} bytes) -> {asset.path}"
        for asset in assets
    )
    return "\n".join(lines)


def upload_assets(
    *,
    release_tag: str,
    assets: typ.Iterable[ReleaseAsset],
    dry_run: bool = False,
    clobber: bool = True,
) -> int:
    """Upload artefacts to GitHub using the ``gh`` CLI.

    Parameters
    ----------
    release_tag
        Git tag identifying the release that should receive the artefacts.
    assets
        Iterable of artefacts to publish.
    dry_run
        When ``True``, print the planned ``gh`` invocations without executing
        them.
    clobber
        When ``True``, overwrite existing assets with the same name.

    Returns
    -------
    int
        Number of assets uploaded (or validated in dry-run mode).

    Raises
    ------
    ProcessExecutionError
        If ``gh`` returns a non-zero status while uploading.
    CommandNotFound
        If the ``gh`` executable is not available in ``PATH``.
    """
    gh_cmd: BoundCommand | None = None
    count = 0

    for asset in assets:
        descriptor = f"{asset.path}#{asset.asset_name}"
        if dry_run:
            print(f"[dry-run] gh release upload {release_tag} {descriptor} --clobber")
            count += 1
            continue

        if gh_cmd is None:
            gh_cmd = local["gh"]

        args = ["release", "upload", release_tag, descriptor]
        if clobber:
            args.append("--clobber")
        gh_cmd[args]()
        count += 1

    return count


def _write_output(name: str, value: str) -> None:
    """Append an output variable for downstream steps."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main(
    *,
    release_tag: str,
    bin_name: str,
    dist_dir: Path = Path("dist"),
    dry_run: bool = False,
    clobber: bool = True,
) -> int:
    """Entry point shared by the CLI and tests.

    Parameters
    ----------
    release_tag
        Git tag identifying the release to publish to.
    bin_name
        Binary name used to derive artefact names during discovery.
    dist_dir
        Directory containing staged artefacts.
    dry_run
        When ``True``, validate artefacts and print the upload plan without
        uploading.
    clobber
        When ``True``, overwrite existing assets with the same name.

    Returns
    -------
    int
        Exit code: ``0`` on success, ``1`` when artefact discovery or upload
        fails.
    """
    try:
        assets = discover_assets(dist_dir, bin_name=bin_name)
    except AssetError as exc:
        print(exc, file=sys.stderr)
        _write_output("uploaded_count", "0")
        _write_output("upload_error", "true")
        _write_output("error_message", str(exc))
        return 1

    if dry_run:
        print(_render_summary(assets))

    try:
        count = upload_assets(
            release_tag=release_tag,
            assets=assets,
            dry_run=dry_run,
            clobber=clobber,
        )
    except (ProcessExecutionError, CommandNotFound) as exc:
        print(exc, file=sys.stderr)
        _write_output("uploaded_count", "0")
        _write_output("upload_error", "true")
        _write_output("error_message", str(exc))
        return 1

    _write_output("uploaded_count", str(count))
    _write_output("upload_error", "false")
    _write_output("error_message", "")
    print(f"Successfully processed {count} asset(s)")
    return 0


@app.default
def cli(
    *,
    release_tag: typ.Annotated[str, Parameter(required=True)],
    bin_name: typ.Annotated[str, Parameter(required=True)],
    dist_dir: Path = Path("dist"),
    dry_run: bool | str = False,
    clobber: bool | str = True,
) -> None:
    """Upload staged artefacts to a GitHub release.

    Discovers artefacts in a staging directory, validates their filenames and
    sizes, and uploads them using the GitHub CLI.
    """
    exit_code = main(
        release_tag=release_tag,
        bin_name=bin_name,
        dist_dir=dist_dir,
        dry_run=_coerce_bool(dry_run, default=False),
        clobber=_coerce_bool(clobber, default=True),
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    app()
