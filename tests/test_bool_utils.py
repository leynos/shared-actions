"""Tests for :mod:`bool_utils`."""

from __future__ import annotations

import pytest

from bool_utils import coerce_bool, coerce_bool_strict


class TestCoerceBool:
    """Tests for the coerce_bool function."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("TRUE", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("YES", True),
            ("on", True),
            ("ON", True),
            ("false", False),
            ("FALSE", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("NO", False),
            ("off", False),
            ("OFF", False),
        ],
    )
    def test_accepts_valid_values(
        self,
        value: bool | str,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Valid boolean-like values are coerced correctly."""
        result = coerce_bool(value, default=False)
        assert result is expected

    def test_returns_default_for_none(self) -> None:
        """None values return the default."""
        assert coerce_bool(None, default=True) is True
        assert coerce_bool(None, default=False) is False

    def test_returns_default_for_empty_string(self) -> None:
        """Empty strings return the default."""
        assert coerce_bool("", default=True) is True
        assert coerce_bool("", default=False) is False

    def test_returns_default_for_whitespace_string(self) -> None:
        """Whitespace-only strings return the default."""
        assert coerce_bool("   ", default=True) is True
        assert coerce_bool("   ", default=False) is False

    def test_raises_for_invalid_string(self) -> None:
        """Invalid strings raise ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret"):
            coerce_bool("maybe", default=False)

    def test_raises_for_invalid_type(self) -> None:
        """Invalid types raise ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret"):
            coerce_bool(42, default=False)  # type: ignore[arg-type]


class TestCoerceBoolStrict:
    """Tests for the coerce_bool_strict function."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("TRUE", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("YES", True),
            ("on", True),
            ("ON", True),
            ("false", False),
            ("FALSE", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("NO", False),
            ("off", False),
            ("OFF", False),
            ("", False),
            ("   ", False),
        ],
    )
    def test_accepts_valid_values(
        self,
        value: bool | str,  # noqa: FBT001
        expected: bool,  # noqa: FBT001
    ) -> None:
        """Valid boolean-like values are coerced correctly."""
        result = coerce_bool_strict(value, parameter="test-param")
        assert result is expected

    def test_raises_for_invalid_string(self) -> None:
        """Invalid strings raise ValueError with parameter name."""
        with pytest.raises(ValueError, match="Invalid value for dry-run"):
            coerce_bool_strict("maybe", parameter="dry-run")

    def test_error_message_includes_value(self) -> None:
        """Error message includes the invalid value."""
        with pytest.raises(ValueError, match="'not-a-bool'"):
            coerce_bool_strict("not-a-bool", parameter="check-tag")
