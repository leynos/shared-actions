#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=2.9"]
# ///

"""Resolve the package version using workflow context or explicit input."""

from __future__ import annotations

import os
import typing as typ

import cyclopts
from _utils import write_env, write_output
from cyclopts import App, Parameter

app = App()
app.config = cyclopts.config.Env("INPUT_", command=False)


@app.default
def main(
    *,
    version: typ.Annotated[str | None, Parameter()] = None,
) -> None:
    """Determine the version string and expose it via `$GITHUB_ENV` and outputs."""
    override = (version or "").strip()
    github_ref = os.environ.get("GITHUB_REF", "")
    github_sha = os.environ.get("GITHUB_SHA", "")

    resolved = ""
    if override:
        resolved = override
    else:
        tag_prefix = "refs/tags/"
        if github_ref.startswith(tag_prefix):
            tag_name = github_ref[len(tag_prefix) :]
            resolved = tag_name.removeprefix("v")
        if not resolved:
            short_sha = github_sha[:7] if github_sha else "unknown"
            resolved = f"0.0.0+{short_sha}"

    write_env("VERSION", resolved)
    write_output("version", resolved)

    print(f"Resolved version: {resolved}")


if __name__ == "__main__":
    app()
