#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9"]
# ///

"""Stage payload files for the macOS installer component package."""

from __future__ import annotations

import gzip
import shutil
import typing as typ
from pathlib import Path

import cyclopts
from _utils import action_work_dir
from cyclopts import App, Parameter

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


def _safe_unlink(path: Path) -> None:
    """Remove files or directories if they already exist."""
    if not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _reset_staging_dirs(*paths: Path) -> None:
    """Clear staging directories before the new build."""
    for path in paths:
        _safe_unlink(path)
        path.mkdir(parents=True, exist_ok=True)


def _normalise_prefix(root: Path, install_prefix: str) -> Path:
    """Ensure the install prefix remains within the package root."""
    prefix = install_prefix.strip()
    relative_prefix = Path(prefix.lstrip("/")) if prefix else Path()
    destination = (root / relative_prefix).resolve()
    root_resolved = root.resolve()
    if destination != root_resolved and root_resolved not in destination.parents:
        msg = f"install_prefix escapes pkgroot: {install_prefix!r}"
        raise ValueError(msg)
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _stage_binary(binary_path: Path, destination_prefix: Path, name: str) -> None:
    dest = destination_prefix / "bin" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary_path, dest)
    dest.chmod(0o755)


def _man_section(manpage_path: Path) -> str:
    name = manpage_path.name.removesuffix(".gz")
    if "." in name:
        section = name.rsplit(".", 1)[-1]
        if section and section[0].isdigit():
            return section
    return "1"


def _stage_manpage(manpage_path: Path, destination_prefix: Path, name: str) -> None:
    section = _man_section(manpage_path)
    man_dir = destination_prefix / "share" / "man" / f"man{section[0]}"
    man_dir.mkdir(parents=True, exist_ok=True)
    man_dest = man_dir / f"{name}.{section}"
    man_dest_gz = Path(f"{man_dest}.gz")

    if manpage_path.suffix == ".gz":
        shutil.copy2(manpage_path, man_dest_gz)
    else:
        with (
            manpage_path.open("rb") as source,
            gzip.GzipFile(
                filename=str(man_dest_gz), mode="wb", compresslevel=9, mtime=0
            ) as target,
        ):
            shutil.copyfileobj(source, target)
    man_dest_gz.chmod(0o644)


def _stage_license(license_path: Path, destination_prefix: Path, name: str) -> None:
    doc_dir = destination_prefix / "share" / "doc" / name
    doc_dir.mkdir(parents=True, exist_ok=True)
    dest_license = doc_dir / "LICENSE"
    shutil.copy2(license_path, dest_license)
    dest_license.chmod(0o644)


@app.default
def main(
    *,
    name: typ.Annotated[str, Parameter(required=True)],
    install_prefix: typ.Annotated[str, Parameter(required=True)],
    binary: typ.Annotated[str, Parameter(required=True)],
    manpage: typ.Annotated[str | None, Parameter()] = None,
    license_file: typ.Annotated[str | None, Parameter()] = None,
) -> None:
    """Create the directory layout expected by `pkgbuild`."""
    binary_path = Path(binary).resolve()
    if not binary_path.is_file():
        msg = f"Binary not found: {binary_path}"
        raise FileNotFoundError(msg)

    work_dir = action_work_dir()
    root = work_dir / "pkgroot"
    build_dir = work_dir / "build"
    resources_dir = work_dir / "Resources"

    _reset_staging_dirs(root, build_dir, resources_dir)
    dist_xml = work_dir / "dist.xml"
    _safe_unlink(dist_xml)

    destination_prefix = _normalise_prefix(root, install_prefix)

    _stage_binary(binary_path, destination_prefix, name)

    if manpage:
        manpage_path = Path(manpage)
        if not manpage_path.is_file():
            msg = f"Manpage not found: {manpage_path}"
            raise FileNotFoundError(msg)
        _stage_manpage(manpage_path, destination_prefix, name)

    if license_file:
        license_path = Path(license_file)
        if not license_path.is_file():
            msg = f"License file not found: {license_path}"
            raise FileNotFoundError(msg)
        _stage_license(license_path, destination_prefix, name)

    print(f"Prepared payload root at {root}")


if __name__ == "__main__":
    app()
