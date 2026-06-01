"""Unit tests for the shared environment-variable helpers in common.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import _env_bool, _required_env  # noqa: I001


# ---------------------------------------------------------------------------
# _required_env
# ---------------------------------------------------------------------------


def test_required_env_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-empty variable value is returned unchanged."""
    monkeypatch.setenv("TEST_VAR", "hello")
    assert _required_env("TEST_VAR") == "hello"


def test_required_env_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leading and trailing whitespace is stripped from the returned value."""
    monkeypatch.setenv("TEST_VAR", "  value  ")
    assert _required_env("TEST_VAR") == "value"


def test_required_env_raises_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset variable causes typer.Exit(2)."""
    monkeypatch.delenv("TEST_VAR", raising=False)
    with pytest.raises(typer.Exit) as exc_info:
        _required_env("TEST_VAR")
    assert exc_info.value.exit_code == 2


def test_required_env_raises_on_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty string causes typer.Exit(2)."""
    monkeypatch.setenv("TEST_VAR", "")
    with pytest.raises(typer.Exit) as exc_info:
        _required_env("TEST_VAR")
    assert exc_info.value.exit_code == 2


def test_required_env_raises_on_whitespace_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only value causes typer.Exit(2)."""
    monkeypatch.setenv("TEST_VAR", "   ")
    with pytest.raises(typer.Exit) as exc_info:
        _required_env("TEST_VAR")
    assert exc_info.value.exit_code == 2


# ---------------------------------------------------------------------------
# _env_bool - unset / empty returns default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("default", [True, False])
def test_env_bool_unset_returns_default(
    monkeypatch: pytest.MonkeyPatch,
    default: bool,  # noqa: FBT001
) -> None:
    """Unset variable returns the supplied default."""
    monkeypatch.delenv("TEST_BOOL", raising=False)
    assert _env_bool("TEST_BOOL", default=default) is default


@pytest.mark.parametrize("default", [True, False])
def test_env_bool_empty_returns_default(
    monkeypatch: pytest.MonkeyPatch,
    default: bool,  # noqa: FBT001
) -> None:
    """Empty string returns the supplied default."""
    monkeypatch.setenv("TEST_BOOL", "")
    assert _env_bool("TEST_BOOL", default=default) is default


@pytest.mark.parametrize("default", [True, False])
def test_env_bool_whitespace_only_returns_default(
    monkeypatch: pytest.MonkeyPatch,
    default: bool,  # noqa: FBT001
) -> None:
    """Whitespace-only value returns the supplied default."""
    monkeypatch.setenv("TEST_BOOL", "   ")
    assert _env_bool("TEST_BOOL", default=default) is default


# ---------------------------------------------------------------------------
# _env_bool - truthy values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["1", "true", "True", "TRUE", "yes", "YES", "Yes", "on", "ON", "On"],
)
def test_env_bool_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """All recognised truthy string representations return True."""
    monkeypatch.setenv("TEST_BOOL", value)
    assert _env_bool("TEST_BOOL", default=False) is True


# ---------------------------------------------------------------------------
# _env_bool - falsy values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "false",
        "False",
        "FALSE",
        "no",
        "NO",
        "No",
        "off",
        "OFF",
        "Off",
    ],
)
def test_env_bool_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """All recognised falsy string representations return False."""
    monkeypatch.setenv("TEST_BOOL", value)
    assert _env_bool("TEST_BOOL", default=True) is False


# ---------------------------------------------------------------------------
# _env_bool - invalid values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["tru", "ye", "nope", "2", "enable", "enabled"])
def test_env_bool_invalid_value_raises(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Any non-empty unrecognised value causes typer.Exit(2)."""
    monkeypatch.setenv("TEST_BOOL", value)
    with pytest.raises(typer.Exit) as exc_info:
        _env_bool("TEST_BOOL", default=False)
    assert exc_info.value.exit_code == 2
