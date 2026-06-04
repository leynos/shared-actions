r"""ANSI escape sequence helpers for test assertions.

Purpose
-------
Provide a shared, deterministic way for tests to remove terminal colour and
style escape sequences before comparing command output. This keeps assertions
focused on the rendered text rather than on presentation codes emitted by
tools under test.

Returns
-------
str
    Plain text with ANSI control sequences removed when using
    :func:`strip_ansi`.

Usage
-----
Call :func:`strip_ansi` with captured output before asserting on it.

Examples
--------
>>> strip_ansi("\x1b[31mfailed\x1b[0m")
'failed'

Public API
----------
strip_ansi
    Return text with ANSI escape sequences removed.
ANSI_ESCAPE_RE
    Compiled regular expression used to match ANSI escape sequences.
"""

from __future__ import annotations

import re

# Match CSI sequences with semicolon- and colon-separated parameters. The
# colon form (e.g. ``\x1b[38:2::255:0:0m``) appears in newer terminals and
# tools.
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")
r"""Compiled pattern for ANSI Control Sequence Introducer escapes.

Parameters
----------
None
    This compiled pattern does not accept parameters.

Returns
-------
re.Pattern[str]
    A compiled regular expression that matches ANSI CSI escape sequences.

Examples
--------
>>> bool(ANSI_ESCAPE_RE.search("\x1b[32mok\x1b[0m"))
True
>>> ANSI_ESCAPE_RE.sub("", "\x1b[1mready\x1b[0m")
'ready'
"""


def strip_ansi(value: str) -> str:
    r"""Return text with ANSI escape sequences removed.

    Parameters
    ----------
    value
        Text that may contain ANSI escape sequences.

    Returns
    -------
    str
        The input text with matched ANSI escape sequences removed.

    Examples
    --------
    >>> strip_ansi("\x1b[31mfailed\x1b[0m")
    'failed'
    >>> strip_ansi("plain")
    'plain'
    """
    return ANSI_ESCAPE_RE.sub("", value)
