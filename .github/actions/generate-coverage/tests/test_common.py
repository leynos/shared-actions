"""Unit tests for the shared environment-variable helpers in common.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import typer
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import _env_bool, _required_env

_WHITESPACE = st.text(alphabet=[" ", "\t", "\n", "\r"], max_size=8)
_VISIBLE_TEXT = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="\x00",
    ),
    min_size=1,
).filter(lambda value: bool(value.strip()))


def _case_variants(values: list[str]) -> st.SearchStrategy[str]:
    """Return generated upper/lower-case combinations for known values."""
    return st.sampled_from(values).flatmap(
        lambda value: st.lists(
            st.booleans(),
            min_size=len(value),
            max_size=len(value),
        ).map(
            lambda flags: "".join(
                char.upper() if make_upper else char.lower()
                for char, make_upper in zip(value, flags, strict=True)
            )
        )
    )


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


@given(prefix=_WHITESPACE, value=_VISIBLE_TEXT, suffix=_WHITESPACE)
def test_required_env_strips_generated_whitespace(
    prefix: str,
    value: str,
    suffix: str,
) -> None:
    """Generated surrounding whitespace is stripped from required env values."""
    original = os.environ.get("TEST_VAR")
    os.environ["TEST_VAR"] = f"{prefix}{value}{suffix}"
    try:
        assert _required_env("TEST_VAR") == value.strip()
    finally:
        if original is None:
            os.environ.pop("TEST_VAR", None)
        else:
            os.environ["TEST_VAR"] = original


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


def _assert_bool_case_insensitive(
    value: str,
    prefix: str,
    suffix: str,
    *,
    default: bool,
    expected: bool,
) -> None:
    """Assert _env_bool handles case variants and surrounding whitespace."""
    original = os.environ.get("TEST_BOOL")
    os.environ["TEST_BOOL"] = f"{prefix}{value}{suffix}"
    try:
        assert _env_bool("TEST_BOOL", default=default) is expected
    finally:
        if original is None:
            os.environ.pop("TEST_BOOL", None)
        else:
            os.environ["TEST_BOOL"] = original


@given(
    value=_case_variants(["1", "true", "yes", "on"]),
    prefix=_WHITESPACE,
    suffix=_WHITESPACE,
)
def test_env_bool_truthy_values_are_case_insensitive(
    value: str,
    prefix: str,
    suffix: str,
) -> None:
    """Generated casing and surrounding whitespace keep truthy values true."""
    _assert_bool_case_insensitive(value, prefix, suffix, default=False, expected=True)


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


@given(
    value=_case_variants(["0", "false", "no", "off"]),
    prefix=_WHITESPACE,
    suffix=_WHITESPACE,
)
def test_env_bool_falsy_values_are_case_insensitive(
    value: str,
    prefix: str,
    suffix: str,
) -> None:
    """Generated casing and surrounding whitespace keep falsy values false."""
    _assert_bool_case_insensitive(value, prefix, suffix, default=True, expected=False)


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
