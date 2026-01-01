"""Shared helpers for GitHub Actions scripts."""

from __future__ import annotations

import os


def normalize_input_env(prefix: str = "INPUT_", *, prefer_dashed: bool = False) -> None:
    """Normalize INPUT_ env vars to avoid duplicate keys like FOO-BAR/FOO_BAR.

    When *prefer_dashed* is true, dashed variants override underscore keys.
    """
    alt_prefix = prefix.replace("_", "-")
    updates: dict[str, str] = {}
    removals: list[str] = []
    for key, value in os.environ.items():
        if not (key.startswith(prefix) or key.startswith(alt_prefix)):
            continue
        normalized = key.replace("-", "_")
        if normalized == key:
            continue
        if prefer_dashed or normalized not in os.environ:
            updates[normalized] = value
        removals.append(key)
    for key, value in updates.items():
        os.environ[key] = value
    for key in removals:
        os.environ.pop(key, None)
