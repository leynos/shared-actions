"""Behavioural tests for the cache-provider guard in rust-build-release.

These run the composite action's validation fragment as a subprocess, so they
cover which provider names the action accepts. The manifest's declared shape
and the forwarding to the nested setup-rust step are covered by
``test_setup_rust_reference.py``.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from rust_build_release_test_helpers import (
    find_step,
    load_action_manifest,
    requires_bash,
)

SUPPORTED_PROVIDERS = frozenset({"github", "external"})
REJECTION_MESSAGE = "cache-provider must be github or external"


def _validation_script() -> str:
    """Return the cache-provider validation shell fragment."""
    steps: list[dict[str, object]] = load_action_manifest()["runs"]["steps"]
    script = find_step(steps, "Validate cache provider").get("run")
    assert isinstance(script, str), "cache-provider validator must be a script"
    return script


def _run_validation(cache_provider: str) -> subprocess.CompletedProcess[str]:
    """Run the cache-provider validator with ``cache_provider``."""
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _validation_script()],
        env={**os.environ, "RBR_CACHE_PROVIDER": cache_provider},
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("cache_provider", sorted(SUPPORTED_PROVIDERS))
def test_accepts_supported_owners(cache_provider: str) -> None:
    """Accept the built-in and caller-owned cache modes."""
    result = _run_validation(cache_provider)

    assert result.returncode == 0, result.stderr


def test_rejects_unknown_owner() -> None:
    """Reject cache modes that would leave ownership ambiguous."""
    result = _run_validation("namespace")

    assert result.returncode != 0
    assert REJECTION_MESSAGE in result.stderr


@example(cache_provider="github")
@example(cache_provider="external")
@example(cache_provider="")
@example(cache_provider="GitHub")
@example(cache_provider=" external ")
@given(
    cache_provider=st.text(
        alphabet=st.characters(min_codepoint=1, max_codepoint=0x7E),
        min_size=0,
        max_size=30,
    )
)
@settings(max_examples=40, derandomize=True, deadline=None)
def test_accepts_exactly_the_supported_domain(cache_provider: str) -> None:
    """Accept only the two exact cache-owner names across bounded text."""
    result = _run_validation(cache_provider)
    is_supported = cache_provider in SUPPORTED_PROVIDERS

    assert (result.returncode == 0) is is_supported
    if not is_supported:
        assert REJECTION_MESSAGE in result.stderr
