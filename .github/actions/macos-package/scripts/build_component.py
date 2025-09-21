#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=2.9", "plumbum"]
# ///

"""Create the component package using `pkgbuild`."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from plumbum import local

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


@app.default
def main(
    *,
    identifier: typ.Annotated[str, Parameter(required=True)],
    version: typ.Annotated[str, Parameter(required=True)],
    name: typ.Annotated[str, Parameter(required=True)],
) -> None:
    """Invoke `pkgbuild` with the resolved package metadata."""
    cwd = Path.cwd()
    root = cwd / "pkgroot"
    if not root.is_dir():
        msg = f"Package root not found: {root}"
        raise FileNotFoundError(msg)

    component_path = cwd / "build" / f"{name}-{version}-component.pkg"
    component_path.parent.mkdir(parents=True, exist_ok=True)

    pkgbuild = local["pkgbuild"]
    pkgbuild[
        "--identifier",
        identifier,
        "--version",
        version,
        "--root",
        str(root),
        "--install-location",
        "/",
        "--ownership",
        "recommended",
        str(component_path),
    ]()

    print(f"Created component package at {component_path}")


if __name__ == "__main__":
    app()
