#!/usr/bin/env -S uv run --script
# fmt: off
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=3.24,<4.0",
#   "syspath-hack>=0.4.0,<0.5.0",
# ]
# ///
# fmt: on

"""Command-line entry point for the staging helper.

Examples
--------
Run the staging helper locally after exporting the required environment
variables::

    export GITHUB_WORKSPACE="$(pwd)"
    export GITHUB_OUTPUT="$(mktemp)"
    INPUT_CONFIG_FILE=.github/release-staging.toml INPUT_TARGET=linux-x86_64 \
        uv run stage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cyclopts
from cyclopts import App
from syspath_hack import prepend_project_root, prepend_to_syspath

# Add script directory to path for stage_common import
_SCRIPT_DIR = Path(__file__).resolve().parent
prepend_to_syspath(_SCRIPT_DIR)

from stage_common import StageError, load_config, require_env_path, stage_artefacts

# Add project root for bool_utils import
prepend_project_root(start=_SCRIPT_DIR)

from bool_utils import coerce_bool

app: App = App(
    help="Stage release artefacts using a TOML configuration file.",
    config=cyclopts.config.Env("INPUT_", command=False),
)


@app.default
def main(
    config_file: str,
    target: str,
    *,
    normalize_windows_paths: str = "false",
) -> None:
    """Stage artefacts for ``target`` using ``config_file``.

    Parameters
    ----------
    config_file
        Path to the project-specific TOML configuration file.
    target
        Target key in the configuration file (for example ``"linux-x86_64"``).
    normalize_windows_paths
        When true, convert backslashes to forward slashes in output paths.

    Raises
    ------
    SystemExit
        Raised with exit code ``1`` when staging cannot proceed because the
        configuration file is missing or the staging pipeline reports an
        error.
    """
    try:
        config_path = Path(config_file)
        github_output = require_env_path("GITHUB_OUTPUT")
        config = load_config(config_path, target)
        normalize = coerce_bool(normalize_windows_paths, default=False)
        result = stage_artefacts(
            config, github_output, normalize_windows_paths=normalize
        )
    except (FileNotFoundError, StageError, ValueError) as exc:
        print(f"::error title=Staging Failure::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    staged_rel = result.staging_dir.relative_to(config.workspace)
    print(
        f"Staged {len(result.staged_artefacts)} artefact(s) into '{staged_rel}'.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    app()
