"""Input normalisation helpers for the validate-linux-packages CLI."""

from __future__ import annotations

import re
import shlex
import typing as typ

from validate_exceptions import ValidationError

__all__ = [
    "dedupe",
    "normalise_command",
    "normalise_formats",
    "normalise_paths",
]


def dedupe(values: typ.Iterable[str]) -> list[str]:
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
                message = f"expected absolute path but received {cleaned!r}"
                raise ValidationError(message)
            paths.append(cleaned)
    return dedupe(paths)


def normalise_command(value: list[str] | None) -> list[str]:
    """Return a cleaned command vector."""
    if not value:
        return []
    tokens: list[str] = []
    for entry in value:
        for line in entry.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            try:
                parts = shlex.split(cleaned)
            except ValueError as exc:  # pragma: no cover - validation surface
                message = f"invalid command segment: {cleaned!r}"
                raise ValidationError(message) from exc
            tokens.extend(parts)
    return tokens
