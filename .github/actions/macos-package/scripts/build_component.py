#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9", "plumbum"]
# ///

"""Create the component package using `pkgbuild`."""

from __future__ import annotations

import typing as typ

import cyclopts
from _utils import action_work_dir
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
    work_dir = action_work_dir()
    root = work_dir / "pkgroot"
    if not root.is_dir():
        msg = f"Package root not found: {root}"
        raise FileNotFoundError(msg)

    component_dir = work_dir / "build"
    component_dir.mkdir(parents=True, exist_ok=True)
    component_path = component_dir / f"{name}-{version}-component.pkg"

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
