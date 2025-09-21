#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9", "plumbum"]
# ///

"""Sign the generated installer package with `productsign`."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import cyclopts
from _utils import write_output
from cyclopts import App, Parameter
from plumbum import local

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


@app.default
def main(
    *,
    name: typ.Annotated[str, Parameter(required=True)],
    version: typ.Annotated[str, Parameter(required=True)],
    developer_id_installer: typ.Annotated[str, Parameter(required=True)],
) -> None:
    """Invoke `productsign` and expose the signed archive path."""
    dist_dir = Path.cwd() / "dist"
    unsigned_pkg = dist_dir / f"{name}-{version}.pkg"
    if not unsigned_pkg.is_file():
        msg = f"Unsigned package not found: {unsigned_pkg}"
        raise FileNotFoundError(msg)

    signed_pkg = dist_dir / f"{name}-{version}-signed.pkg"
    if signed_pkg.exists():
        signed_pkg.unlink()

    productsign = local["productsign"]
    productsign[
        "--sign",
        developer_id_installer,
        str(unsigned_pkg),
        str(signed_pkg),
    ]()

    write_output("signed_pkg_path", str(signed_pkg))
    print(f"Signed package at {signed_pkg}")


if __name__ == "__main__":
    app()
