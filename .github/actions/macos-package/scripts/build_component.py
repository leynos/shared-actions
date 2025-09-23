#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9", "plumbum"]
# ///

"""Create the component package using `pkgbuild`."""

from __future__ import annotations

import typing as typ

from _utils import (
    ActionError,
    Parameter,
    action_work_dir,
    configure_app,
    remove_file,
    run_app,
)
from plumbum import local

app = configure_app()


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
        raise ActionError(msg)

    component_dir = work_dir / "build"
    component_dir.mkdir(parents=True, exist_ok=True)
    component_path = component_dir / f"{name}-{version}-component.pkg"
    remove_file(component_path, context=f"component package '{component_path}'")

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
    run_app(app)
