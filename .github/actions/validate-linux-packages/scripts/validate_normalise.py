"""Input normalisation helpers for the validate-linux-packages CLI."""

from __future__ import annotations

import re
from typing import Iterable

from validate_exceptions import ValidationError

__all__ = [
    "dedupe",
    "normalise_command",
    "normalise_formats",
    "normalise_paths",
]


def dedupe(values: Iterable[str]) -> list[str]:
    """Return ``values`` without duplicates while preserving order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalise_formats(values: list[str] | None) -> list[str]:
    """Return ordered, deduplicated, lower-cased formats."""

    if not values:
        return ["deb"]
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in values:
        for token in re.split(r"[\s,]+", entry.strip()):
            if not token:
                continue
            lowered = token.lower()
            if lowered not in seen:
                seen.add(lowered)
                ordered.append(lowered)
    return ordered


def normalise_paths(values: list[str] | None) -> list[str]:
    """Return absolute paths derived from ``values`` while preserving order."""

    if not values:
        return []
    paths: list[str] = []
    for entry in values:
        for line in entry.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if not cleaned.startswith("/"):
                raise ValidationError(
                    f"expected absolute path but received {cleaned!r}"
                )
            paths.append(cleaned)
    return dedupe(paths)


def normalise_command(value: list[str] | None) -> list[str]:
    """Return a cleaned command vector."""

    if not value:
        return []
    return [part for part in (item.strip() for item in value) if part]
