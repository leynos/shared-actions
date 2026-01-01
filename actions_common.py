"""Shared helpers for GitHub Actions scripts."""

from __future__ import annotations

import os


def _is_dashed_input_key(key: str, prefix: str, alt_prefix: str) -> bool:
    """Return True if key is a dashed variant of an input key."""
    if not key.startswith((prefix, alt_prefix)):
        return False
    return "-" in key


def _should_update_normalized(normalized: str, *, prefer_dashed: bool) -> bool:
    """Return True if the normalized key should be updated."""
    return prefer_dashed or normalized not in os.environ


def normalize_input_env(prefix: str = "INPUT_", *, prefer_dashed: bool = False) -> None:
    """Normalize INPUT_ env vars to avoid duplicate keys like FOO-BAR/FOO_BAR.

    When *prefer_dashed* is true, dashed variants override underscore keys.
    """
    alt_prefix = prefix.replace("_", "-")
    updates: dict[str, str] = {}
    removals: list[str] = []

    for key, value in os.environ.items():
        if not _is_dashed_input_key(key, prefix, alt_prefix):
            continue
        normalized = key.replace("-", "_")
        if _should_update_normalized(normalized, prefer_dashed=prefer_dashed):
            updates[normalized] = value
        removals.append(key)

    for key, value in updates.items():
        os.environ[key] = value
    for key in removals:
        os.environ.pop(key, None)
