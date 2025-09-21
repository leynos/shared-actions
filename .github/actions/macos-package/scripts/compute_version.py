#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9"]
# ///

"""Resolve the package version using workflow context or explicit input."""

from __future__ import annotations

import os
import typing as typ

import cyclopts
from _utils import write_env, write_output
from cyclopts import App, Parameter

TAG_VERSION_PREFIX_ENV = "TAG_VERSION_PREFIX"

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
    tag_version_prefix = os.environ.get(TAG_VERSION_PREFIX_ENV, "v")

    resolved = ""
    build_metadata = ""
    if override:
        resolved = override.removeprefix("v")
    else:
        tag_prefix = "refs/tags/"
        if github_ref.startswith(tag_prefix):
            tag_name = github_ref[len(tag_prefix) :]
            if tag_version_prefix and tag_name.startswith(tag_version_prefix):
                resolved = tag_name[len(tag_version_prefix) :]
            else:
                resolved = tag_name
        if not resolved:
            resolved = "0.0.0"
            build_metadata = github_sha[:7] if github_sha else ""

    write_env("VERSION", resolved)
    write_output("version", resolved)
    if build_metadata:
        write_env("VERSION_BUILD_METADATA", build_metadata)
        write_output("version_build_metadata", build_metadata)

    message = f"Resolved version: {resolved}"
    if build_metadata:
        message += f" (build metadata: {build_metadata})"
    print(message)


if __name__ == "__main__":
    app()
