"""Boolean coercion utilities for GitHub Actions inputs.

GitHub Actions forwards workflow_call inputs as strings, so these helpers
accept a variety of truthy/falsy spellings and convert them to bool.
"""

from __future__ import annotations

__all__ = ["coerce_bool", "coerce_bool_strict"]

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def coerce_bool(value: object, *, default: bool) -> bool:
    """Coerce a value to bool, returning default for None/empty.

    Parameters
    ----------
    value
        The value to coerce. Accepts bool, str, or None.
    default
        The value to return when ``value`` is None or an empty string.

    Returns
    -------
    bool
        The coerced boolean value.

    Raises
    ------
    ValueError
        If the value is a string that cannot be interpreted as a boolean.

    Examples
    --------
    >>> coerce_bool("true", default=False)
    True
    >>> coerce_bool(None, default=True)
    True
    >>> coerce_bool("", default=False)
    False
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().lower()
        if not normalised:
            return default
        if normalised in _TRUTHY:
            return True
        if normalised in _FALSY:
            return False
    msg = f"Cannot interpret {value!r} as boolean"
    raise ValueError(msg)


def coerce_bool_strict(value: bool | str, *, parameter: str) -> bool:  # noqa: FBT001
    """Coerce a value to bool, raising ValueError with parameter name on failure.

    Unlike :func:`coerce_bool`, this function treats empty strings as False
    rather than falling back to a default, and includes the parameter name
    in error messages.

    Parameters
    ----------
    value
        The value to coerce. Accepts bool or str.
    parameter
        The name of the parameter, used in error messages.

    Returns
    -------
    bool
        The coerced boolean value.

    Raises
    ------
    ValueError
        If the value cannot be interpreted as a boolean.

    Examples
    --------
    >>> coerce_bool_strict("true", parameter="dry-run")
    True
    >>> coerce_bool_strict("", parameter="dry-run")
    False
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in _TRUTHY:
            return True
        if normalised in {*_FALSY, ""}:
            return False
    msg = f"Invalid value for {parameter}: {value!r}. Expected a boolean-like string."
    raise ValueError(msg)
