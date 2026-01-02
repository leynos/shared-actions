"""Tests for shared GitHub Actions helpers."""

from __future__ import annotations

import os
import typing as typ

from actions_common import normalize_input_env

if typ.TYPE_CHECKING:
    import pytest


def test_normalize_input_env_promotes_dashed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashed INPUT_ keys are promoted when underscore variants are missing."""
    monkeypatch.setenv("INPUT-FOO-BAR", "value")
    monkeypatch.delenv("INPUT_FOO_BAR", raising=False)

    normalize_input_env()

    assert os.environ["INPUT_FOO_BAR"] == "value"
    assert "INPUT-FOO-BAR" not in os.environ


def test_normalize_input_env_prefers_existing_underscore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Underscore variants win when both dashed and underscore keys exist."""
    monkeypatch.setenv("INPUT_FOO_BAR", "keep")
    monkeypatch.setenv("INPUT-FOO-BAR", "drop")

    normalize_input_env()

    assert os.environ["INPUT_FOO_BAR"] == "keep"
    assert "INPUT-FOO-BAR" not in os.environ


def test_normalize_input_env_respects_empty_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty underscore values are preserved and dashed keys removed."""
    monkeypatch.setenv("INPUT_FOO_BAR", "")
    monkeypatch.setenv("INPUT-FOO-BAR", "value")

    normalize_input_env()

    assert os.environ["INPUT_FOO_BAR"] == ""
    assert "INPUT-FOO-BAR" not in os.environ


def test_normalize_input_env_prefers_dashed_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashed keys override underscore variants when prefer_dashed is enabled."""
    monkeypatch.setenv("INPUT_FOO_BAR", "keep")
    monkeypatch.setenv("INPUT-FOO-BAR", "override")

    normalize_input_env(prefer_dashed=True)

    assert os.environ["INPUT_FOO_BAR"] == "override"
    assert "INPUT-FOO-BAR" not in os.environ
