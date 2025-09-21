#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=2.9"]
# ///

"""Stage payload files for the macOS installer component package."""

from __future__ import annotations

import gzip
import shutil
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


def _safe_unlink(path: Path) -> None:
    """Remove files or directories if they already exist."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


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
    cwd = Path.cwd()
    root = cwd / "pkgroot"
    build_dir = cwd / "build"
    dist_dir = cwd / "dist"
    resources_dir = cwd / "Resources"

    for path in (root, build_dir, dist_dir, resources_dir):
        if path.exists():
            _safe_unlink(path)

    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)

    prefix = install_prefix.strip()
    relative_prefix = Path(prefix.lstrip("/")) if prefix else Path()
    destination_prefix = root / relative_prefix

    binary_path = Path(binary).resolve()
    if not binary_path.is_file():
        msg = f"Binary not found: {binary_path}"
        raise FileNotFoundError(msg)

    dest_bin_dir = destination_prefix / "bin"
    dest_bin_dir.mkdir(parents=True, exist_ok=True)
    dest_binary = dest_bin_dir / name
    shutil.copy2(binary_path, dest_binary)
    dest_binary.chmod(0o755)

    manpage_path = Path(manpage) if manpage else None
    if manpage_path:
        if not manpage_path.is_file():
            msg = f"Manpage not found: {manpage_path}"
            raise FileNotFoundError(msg)

        sec = manpage_path.name.split(".")[-1] if "." in manpage_path.name else ""
        if not sec or not sec[0].isdigit():
            sec = "1"
        man_section_dir = destination_prefix / "share" / "man" / f"man{sec[0]}"
        man_section_dir.mkdir(parents=True, exist_ok=True)
        man_dest = man_section_dir / f"{name}.{sec}"
        man_dest_gz = Path(f"{man_dest}.gz")

        if manpage_path.name.endswith(".gz"):
            shutil.copy2(manpage_path, man_dest_gz)
        else:
            with manpage_path.open("rb") as source, gzip.GzipFile(
                filename=man_dest_gz, mode="wb", compresslevel=9, mtime=0
            ) as target:
                shutil.copyfileobj(source, target)
        man_dest_gz.chmod(0o644)

    license_path = Path(license_file) if license_file else None
    if license_path:
        if not license_path.is_file():
            msg = f"License file not found: {license_path}"
            raise FileNotFoundError(msg)

        doc_dir = destination_prefix / "share" / "doc" / name
        doc_dir.mkdir(parents=True, exist_ok=True)
        dest_license = doc_dir / "LICENSE"
        shutil.copy2(license_path, dest_license)
        dest_license.chmod(0o644)

    print(f"Prepared payload root at {root}")


if __name__ == "__main__":
    app()
