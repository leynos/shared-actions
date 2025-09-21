#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9", "plumbum"]
# ///

"""Wrap the component package into a distributable product archive."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import cyclopts
from _utils import action_work_dir, write_output
from cyclopts import App, Parameter
from plumbum import local

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


@app.default
def main(
    *,
    name: typ.Annotated[str, Parameter(required=True)],
    version: typ.Annotated[str, Parameter(required=True)],
    include_license_panel: typ.Annotated[bool, Parameter()] = False,
) -> None:
    """Call `productbuild` and expose the resulting archive path."""
    cwd = Path.cwd()
    work_dir = action_work_dir()
    build_dir = work_dir / "build"
    component = build_dir / f"{name}-{version}-component.pkg"
    if not component.is_file():
        msg = f"Component package not found: {component}"
        raise FileNotFoundError(msg)

    dist_dir = cwd / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    output_pkg = dist_dir / f"{name}-{version}.pkg"
    if output_pkg.exists():
        output_pkg.unlink()

    productbuild = local["productbuild"]
    if include_license_panel:
        distribution = work_dir / "dist.xml"
        resources = work_dir / "Resources"
        if not distribution.is_file():
            msg = "Distribution XML missing; run license preparation step"
            raise FileNotFoundError(msg)
        if not resources.is_dir():
            msg = "Resources directory missing; run license preparation step"
            raise FileNotFoundError(msg)

        productbuild[
            "--distribution",
            str(distribution),
            "--resources",
            str(resources),
            "--package-path",
            str(build_dir),
            str(output_pkg),
        ]()
    else:
        productbuild[
            "--package",
            str(component),
            str(output_pkg),
        ]()

    write_output("pkg_path", str(output_pkg))
    print(f"Created product archive at {output_pkg}")


if __name__ == "__main__":
    app()
