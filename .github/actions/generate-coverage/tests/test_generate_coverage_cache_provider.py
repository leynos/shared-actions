"""Test cache ownership in the generate-coverage composite action."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml
from hypothesis import example, given, settings
from hypothesis import strategies as st

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


def _run_validation(cache_provider: str) -> subprocess.CompletedProcess[str]:
    """Run the cache-provider validator with ``cache_provider``."""
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [_requires_bash(), "-c", _validation_script()],
        env={**os.environ, "GC_CACHE_PROVIDER": cache_provider},
        capture_output=True,
        text=True,
        timeout=10,
    )


def _cache_report_script() -> str:
    """Return the bounded cache-decision reporting shell fragment."""
    script = _step("Report archive cache decisions").get("run")
    assert isinstance(script, str), "cache reporter must be a script"
    return script


def _run_cache_report(
    **report_environment: str,
) -> subprocess.CompletedProcess[str]:
    """Run the cache reporter with explicit action observations."""
    environment = {**os.environ, **report_environment}
    environment.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [_requires_bash(), "-c", _cache_report_script()],
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
    cache_provider = _load_action()["inputs"]["cache-provider"]
    assert cache_provider["default"] == "github"


def test_external_cache_disables_overlapping_archive_caches() -> None:
    """Make the caller the sole owner of Rust and uv cache paths."""
    setup_uv = _step("Setup uv")
    cargo_cache = _step("Cache cargo artefacts")
    python_cache = _step("Cache Python deps")

    assert setup_uv["with"]["enable-cache"] == (
        "${{ inputs.cache-provider == 'github' && "
        "runner.environment == 'github-hosted' }}"
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


@pytest.mark.parametrize(
    ("environment", "expected_notice"),
    [
        (
            {
                "GC_CACHE_PROVIDER": "external",
                "GC_LANGUAGE": "mixed",
                "GC_RUNNER_ENVIRONMENT": "self-hosted",
                "GC_UV_STEP_OUTCOME": "success",
                "GC_UV_CACHE_HIT": "true",
                "GC_CARGO_STEP_OUTCOME": "skipped",
                "GC_CARGO_CACHE_HIT": "",
                "GC_PYTHON_STEP_OUTCOME": "skipped",
                "GC_PYTHON_CACHE_HIT": "",
            },
            "provider=external cargo=disabled python=disabled uv=disabled",
        ),
        (
            {
                "GC_CACHE_PROVIDER": "github",
                "GC_LANGUAGE": "mixed",
                "GC_RUNNER_ENVIRONMENT": "github-hosted",
                "GC_UV_STEP_OUTCOME": "success",
                "GC_UV_CACHE_HIT": "true",
                "GC_CARGO_STEP_OUTCOME": "success",
                "GC_CARGO_CACHE_HIT": "false",
                "GC_PYTHON_STEP_OUTCOME": "success",
                "GC_PYTHON_CACHE_HIT": "true",
            },
            "provider=github cargo=miss python=hit uv=hit",
        ),
        (
            {
                "GC_CACHE_PROVIDER": "github",
                "GC_LANGUAGE": "mixed",
                "GC_RUNNER_ENVIRONMENT": "github-hosted",
                "GC_UV_STEP_OUTCOME": "failure",
                "GC_UV_CACHE_HIT": "",
                "GC_CARGO_STEP_OUTCOME": "failure",
                "GC_CARGO_CACHE_HIT": "",
                "GC_PYTHON_STEP_OUTCOME": "failure",
                "GC_PYTHON_CACHE_HIT": "",
            },
            "provider=github cargo=error python=error uv=error",
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
