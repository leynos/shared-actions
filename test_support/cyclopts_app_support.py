"""Small adapters for invoking Cyclopts apps in tests."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _invoke_cyclopts_successfully(
    app: cabc.Callable[..., object], *args: object, **kwargs: object
) -> None:
    """Invoke an app and accept only normal return or a zero-status exit."""
    try:
        app(*args, **kwargs)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
