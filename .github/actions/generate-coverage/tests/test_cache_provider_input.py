"""Test cache ownership in the generate-coverage composite action."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"


def _load_action() -> dict[str, typ.Any]:
    """Return the generate-coverage action contract."""
    loaded = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _step(name: str) -> dict[str, typ.Any]:
    """Return the single composite step named ``name``."""
    steps = _load_action()["runs"]["steps"]
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step"
    return matches[0]


def _validation_script() -> str:
    """Return the cache-provider validation shell fragment."""
    script = _step("Validate cache provider").get("run")
    assert isinstance(script, str)
    return script


def _requires_bash() -> str:
    """Return a usable bash path or skip shell-fragment tests."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


@pytest.mark.parametrize("cache_provider", ["github", "external"])
def test_cache_provider_accepts_supported_owners(cache_provider: str) -> None:
    """Accept the built-in and caller-owned cache modes."""
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [_requires_bash(), "-c", _validation_script()],
        env={**os.environ, "GC_CACHE_PROVIDER": cache_provider},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_cache_provider_rejects_unknown_owner() -> None:
    """Reject cache modes that would leave ownership ambiguous."""
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [_requires_bash(), "-c", _validation_script()],
        env={**os.environ, "GC_CACHE_PROVIDER": "namespace"},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "cache-provider must be github or external" in result.stderr


def test_cache_provider_defaults_to_github() -> None:
    """Preserve GitHub caching for existing callers."""
    cache_provider = _load_action()["inputs"]["cache-provider"]
    assert cache_provider["default"] == "github"


def test_external_cache_disables_overlapping_archive_caches() -> None:
    """Make the caller the sole owner of Rust and uv cache paths."""
    setup_uv = _step("Setup uv")
    cargo_cache = _step("Cache cargo artefacts")
    python_cache = _step("Cache Python deps")

    assert setup_uv["with"]["enable-cache"] == (
        "${{ inputs.cache-provider == 'github' }}"
    )
    assert cargo_cache["if"] == (
        "inputs.cache-provider == 'github' && "
        "(steps.detect.outputs.lang == 'rust' || steps.detect.outputs.lang == 'mixed')"
    )
    assert python_cache["if"] == (
        "inputs.cache-provider == 'github' && "
        "(steps.detect.outputs.lang == 'python' || "
        "steps.detect.outputs.lang == 'mixed')"
    )


def test_ratchet_baseline_cache_remains_available_in_external_mode() -> None:
    """Keep baseline state outside the caller-owned Rust and uv mounts."""
    restore = _step("Restore baselines")
    save = _step("Save baselines")

    assert "cache-provider" not in restore["if"]
    assert "cache-provider" not in save["if"]
