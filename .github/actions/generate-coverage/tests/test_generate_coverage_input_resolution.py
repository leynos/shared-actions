"""Tests for resolving coverage action inputs from CLI and environment."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from script_loader import load_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


@pytest.fixture
def run_rust(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module."""
    return load_script_module(monkeypatch, "run_rust")


#: A ``_RawInputs`` field value: the CLI leaves each one unset by default.
RawInputValue = str | bool | Path | None


def _raw_inputs(run_rust: ModuleType, **overrides: RawInputValue) -> object:
    """Build a ``_RawInputs`` record with every field defaulted to unset."""
    defaults: dict[str, RawInputValue] = {
        "output_path": None,
        "features": None,
        "with_default": None,
        "use_nextest": None,
        "lang": None,
        "fmt": None,
        "manifest_path": None,
        "github_output": None,
        "cucumber_rs_features": None,
        "cucumber_rs_args": None,
        "extra_cargo_args": None,
        "with_cucumber_rs": None,
        "all_features": None,
        "all_targets": None,
        "doctests": None,
        "allow_no_tests": None,
        "baseline_file": None,
    }
    return run_rust._RawInputs(**{**defaults, **overrides})


def test_resolvers_read_only_the_mapping_they_are_given(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolvers must be functions of their arguments, not the process.

    Ambient values are set to the opposite of the supplied mapping, so a
    resolver that reached for ``os.environ`` would produce the wrong answer
    rather than merely an unverified one.
    """
    for name, value in (
        ("DETECTED_LANG", "python"),
        ("DETECTED_FMT", "coveragepy"),
        ("DETECTED_CARGO_MANIFEST", "ambient/Cargo.toml"),
        ("INPUT_OUTPUT_PATH", "/ambient/cov.xml"),
        ("GITHUB_OUTPUT", "/ambient/gh.txt"),
        ("INPUT_FEATURES", "ambient"),
        ("INPUT_WITH_DEFAULT_FEATURES", "true"),
        ("INPUT_USE_CARGO_NEXTEST", "true"),
        ("INPUT_ALL_FEATURES", "true"),
        ("INPUT_ALL_TARGETS", "true"),
        ("INPUT_DOCTESTS", "true"),
        ("INPUT_WITH_CUCUMBER_RS", "true"),
        ("INPUT_CUCUMBER_RS_FEATURES", "ambient/features"),
        ("INPUT_CUCUMBER_RS_ARGS", "--tag ambient"),
        ("INPUT_EXTRA_CARGO_ARGS", "--exclude ambient-crate"),
    ):
        monkeypatch.setenv(name, value)

    env = {
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "DETECTED_CARGO_MANIFEST": "crates/api/Cargo.toml",
        "INPUT_OUTPUT_PATH": "cov.lcov",
        "GITHUB_OUTPUT": "gh.txt",
        "INPUT_FEATURES": "cli",
        "INPUT_WITH_DEFAULT_FEATURES": "false",
        "INPUT_USE_CARGO_NEXTEST": "false",
        "INPUT_ALL_FEATURES": "false",
        "INPUT_ALL_TARGETS": "false",
        "INPUT_DOCTESTS": "false",
        "INPUT_WITH_CUCUMBER_RS": "false",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "INPUT_CUCUMBER_RS_ARGS": "--tag fast",
        "INPUT_EXTRA_CARGO_ARGS": "--exclude scoped-crate",
    }
    raw = _raw_inputs(run_rust)

    targets = run_rust._resolve_targets(raw, env)
    selection = run_rust._resolve_features(raw, env)
    cucumber = run_rust._resolve_cucumber(raw, env)

    assert targets.lang == "rust"
    assert targets.fmt == "lcov"
    assert targets.manifest_path == Path("crates/api/Cargo.toml")
    assert targets.github_output == Path("gh.txt")
    assert selection.features == "cli"
    assert selection.with_default is False
    assert selection.use_nextest is False
    assert selection.all_features is False
    assert selection.all_targets is False
    assert selection.doctests is False
    assert selection.extra_cargo_args == ("--exclude", "scoped-crate")
    assert cucumber.enabled is False
    assert cucumber.features == "tests/features"
    assert cucumber.args == "--tag fast"


def test_resolvers_apply_defaults_for_an_empty_mapping(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty mapping must yield the documented defaults, not ambient state."""
    for name, value in (
        ("INPUT_FEATURES", "ambient"),
        ("INPUT_WITH_DEFAULT_FEATURES", "false"),
        ("INPUT_USE_CARGO_NEXTEST", "false"),
        ("INPUT_ALL_FEATURES", "true"),
        ("INPUT_ALL_TARGETS", "true"),
        ("INPUT_DOCTESTS", "true"),
        ("INPUT_EXTRA_CARGO_ARGS", "--exclude ambient-crate"),
    ):
        monkeypatch.setenv(name, value)

    selection = run_rust._resolve_features(_raw_inputs(run_rust), {})

    assert selection.features == ""
    assert selection.with_default is True
    assert selection.use_nextest is True
    assert selection.all_features is False
    assert selection.all_targets is False
    assert selection.doctests is False
    assert selection.extra_cargo_args == ()


def test_explicit_inputs_win_over_the_mapping(run_rust: ModuleType) -> None:
    """A value passed on the command line must beat the environment."""
    env = {
        "INPUT_FEATURES": "from-env",
        "INPUT_ALL_TARGETS": "true",
        "INPUT_EXTRA_CARGO_ARGS": "--exclude env-crate",
    }
    raw = _raw_inputs(
        run_rust,
        features="from-cli",
        all_targets=False,
        extra_cargo_args="--package cli-crate",
    )

    selection = run_rust._resolve_features(raw, env)

    assert selection.features == "from-cli"
    assert selection.all_targets is False
    assert selection.extra_cargo_args == ("--package", "cli-crate")
