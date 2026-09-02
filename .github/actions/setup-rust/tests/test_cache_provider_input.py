"""Test cache ownership in the setup-rust composite action."""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml
from hypothesis import example, given, settings
from hypothesis import strategies as st
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


def _run_validation(cache_provider: str) -> subprocess.CompletedProcess[str]:
    """Run the cache-provider validator with ``cache_provider``."""
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _cache_provider_validation_script()],
        env={**os.environ, "SR_CACHE_PROVIDER": cache_provider},
        capture_output=True,
        text=True,
        timeout=10,
    )


def _cache_report_script() -> str:
    """Return the bounded cache-decision reporting shell fragment."""
    script = get_step("Report archive cache decisions").get("run")
    assert isinstance(script, str), "cache reporter must be a script"
    return script


def _run_cache_report(
    **report_environment: str,
) -> subprocess.CompletedProcess[str]:
    """Run the cache reporter with explicit action observations."""
    environment = {**os.environ, **report_environment}
    environment.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _cache_report_script()],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("cache_provider", ["github", "external"])
def test_cache_provider_accepts_supported_owners(cache_provider: str) -> None:
    """Accept the built-in and caller-owned cache modes."""
    result = _run_validation(cache_provider)

    assert result.returncode == 0, result.stderr


def test_cache_provider_rejects_unknown_owner() -> None:
    """Reject cache modes that would leave ownership ambiguous."""
    result = _run_validation("namespace")

    assert result.returncode != 0
    assert "cache-provider must be github or external" in result.stderr


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
def test_cache_provider_accepts_exactly_the_supported_domain(
    cache_provider: str,
) -> None:
    """Accept only the two exact cache-owner names across bounded text."""
    result = _run_validation(cache_provider)
    is_supported = cache_provider in {"github", "external"}

    assert (result.returncode == 0) is is_supported
    if not is_supported:
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
        "${{ inputs.cache-provider == 'github' && "
        "runner.environment == 'github-hosted' }}"
    )


@pytest.mark.parametrize(
    ("environment", "expected_notice"),
    [
        (
            {
                "SR_CACHE_PROVIDER": "external",
                "SR_RUNNER_ENVIRONMENT": "self-hosted",
                "SR_UV_STEP_OUTCOME": "success",
                "SR_UV_CACHE_HIT": "true",
                "SR_CARGO_STEP_OUTCOME": "skipped",
                "SR_CARGO_CACHE_HIT": "",
            },
            "provider=external cargo=disabled uv=disabled",
        ),
        (
            {
                "SR_CACHE_PROVIDER": "github",
                "SR_RUNNER_ENVIRONMENT": "github-hosted",
                "SR_UV_STEP_OUTCOME": "success",
                "SR_UV_CACHE_HIT": "false",
                "SR_CARGO_STEP_OUTCOME": "success",
                "SR_CARGO_CACHE_HIT": "true",
            },
            "provider=github cargo=hit uv=miss",
        ),
        (
            {
                "SR_CACHE_PROVIDER": "github",
                "SR_RUNNER_ENVIRONMENT": "github-hosted",
                "SR_UV_STEP_OUTCOME": "failure",
                "SR_UV_CACHE_HIT": "",
                "SR_CARGO_STEP_OUTCOME": "failure",
                "SR_CARGO_CACHE_HIT": "",
            },
            "provider=github cargo=error uv=error",
        ),
    ],
)
def test_cache_report_uses_bounded_outcomes(
    environment: dict[str, str], expected_notice: str
) -> None:
    """Report enabled, disabled, and failed cache decisions without raw data."""
    result = _run_cache_report(**environment)

    assert result.returncode == 0, result.stderr
    assert expected_notice in result.stdout
