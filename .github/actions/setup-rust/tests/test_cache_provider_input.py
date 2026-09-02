"""Test cache ownership in the setup-rust composite action."""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash


def _load_inputs() -> dict[str, object]:
    """Load the action input declarations."""
    manifest = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return manifest["inputs"]


def _cache_provider_validation_script() -> str:
    """Return the cache-provider validation shell fragment."""
    script = get_step("Validate cache provider").get("run")
    assert isinstance(script, str), "cache-provider validator must be a script"
    return script


@pytest.mark.parametrize("cache_provider", ["github", "external"])
def test_cache_provider_accepts_supported_owners(cache_provider: str) -> None:
    """Accept the built-in and caller-owned cache modes."""
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _cache_provider_validation_script()],
        env={**os.environ, "SR_CACHE_PROVIDER": cache_provider},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_cache_provider_rejects_unknown_owner() -> None:
    """Reject cache modes that would leave ownership ambiguous."""
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _cache_provider_validation_script()],
        env={**os.environ, "SR_CACHE_PROVIDER": "namespace"},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "cache-provider must be github or external" in result.stderr


def test_cache_provider_defaults_to_github() -> None:
    """Preserve GitHub caching for existing callers."""
    cache_provider = _load_inputs()["cache-provider"]
    assert isinstance(cache_provider, dict)
    assert cache_provider.get("default") == "github"


def test_external_cache_disables_nested_archive_caches() -> None:
    """Make the caller the sole Cargo and uv cache owner in external mode."""
    cargo_cache = get_step("Cache cargo registry")
    setup_uv = get_step("Install uv")

    assert cargo_cache.get("if") == "${{ inputs.cache-provider == 'github' }}"
    setup_uv_inputs = setup_uv.get("with")
    assert isinstance(setup_uv_inputs, dict)
    assert setup_uv_inputs.get("enable-cache") == (
        "${{ inputs.cache-provider == 'github' }}"
    )
