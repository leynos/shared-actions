"""Helpers for writing GitHub Actions environment files and outputs."""

from __future__ import annotations

from pathlib import Path


def append_key_value(path: Path, key: str, value: str) -> None:
    """Append a ``key=value`` pair to the given file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def write_output(key: str, value: str) -> None:
    """Write an output variable for the current GitHub step."""
    from os import environ

    output_path = environ.get("GITHUB_OUTPUT")
    if output_path:
        append_key_value(Path(output_path), key, value)


def write_env(key: str, value: str) -> None:
    """Write an environment variable for subsequent GitHub steps."""
    from os import environ

    env_path = environ.get("GITHUB_ENV")
    if env_path:
        append_key_value(Path(env_path), key, value)
