"""Contract for the Python dependency cache in ``generate-coverage``.

A warm restore of ``~/.cache/uv`` once handed a lane script environments that
another runner type, or another Python, had built, and ``uv venv --python``
on one of them exited 2 (issue #547). Two properties keep that from
recurring, and each is asserted on its own so that removing one cannot hide
behind the other:

- the cache never carries ``environments-v2``, the directory of interpreter
  bound script environments, while still caching the rest of ``~/.cache/uv``;
- the key, and every restore key, names the runner environment and the
  resolved interpreter, so a restore cannot cross either boundary.

Restore keys are read separately because they are prefixes: a restore key
shorter than the key would restore across exactly the boundary the key draws.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "actions"
    / "generate-coverage"
    / "action.yml"
)
CACHE_STEP = "Cache Python deps"
UV_CACHE = "~/.cache/uv"
EXCLUDED = "!~/.cache/uv/environments-v2"
#: What a key must interpolate so a restore cannot cross the boundary it names.
BOUNDARY_TOKENS = (
    "runner.environment",
    "runner.arch",
    "steps.interpreter.outputs.version",
)


def _cache_inputs() -> dict[str, typ.Any]:
    """Return the ``with`` mapping of the Python dependency cache step."""
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    matching = [step for step in steps if step.get("name") == CACHE_STEP]
    assert len(matching) == 1, f"expected one {CACHE_STEP!r} step, got {len(matching)}"
    return matching[0]["with"]


def _lines(value: str) -> list[str]:
    """Return the non-empty, stripped lines of a multi-line input."""
    return [line.strip() for line in value.splitlines() if line.strip()]


def test_the_cache_excludes_interpreter_bound_environments() -> None:
    """``environments-v2`` is excluded, and the rest of the uv cache is kept.

    The second half matters as much as the first: an exclusion written by
    dropping the positive path as well would pass the first assertion and
    turn the cache into a no-op that nobody notices.
    """
    paths = _lines(_cache_inputs()["path"])

    assert EXCLUDED in paths, f"{EXCLUDED} must be excluded, got {paths}"
    assert UV_CACHE in paths, f"{UV_CACHE} must still be cached, got {paths}"


@pytest.mark.parametrize("token", BOUNDARY_TOKENS)
def test_the_key_names_each_boundary_a_restore_must_not_cross(token: str) -> None:
    """The key interpolates the runner environment, architecture and Python."""
    key = _cache_inputs()["key"]

    assert token in key, f"cache key must interpolate {token}, got {key!r}"


@pytest.mark.parametrize("token", BOUNDARY_TOKENS)
def test_every_restore_key_repeats_the_boundaries(token: str) -> None:
    """A restore key carries the same boundaries as the key it falls back from.

    Restore keys match by prefix, so one that stops short of the boundary
    restores an entry built on the far side of it.
    """
    restore_keys = _lines(_cache_inputs()["restore-keys"])

    assert restore_keys, "the cache must declare restore keys"
    for restore_key in restore_keys:
        assert token in restore_key, (
            f"restore key must interpolate {token}, got {restore_key!r}"
        )
