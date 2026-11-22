"""Reading a bounded command's output for the one line a finding quotes.

A command's streams are bounded by the runner, so the text a finding selects is
already the retained prefix. What remains is choosing which of its lines names
the failure: a compiler names it first and explains afterwards, while a
traceback names it last. The truncation marker the bound writes is never a
diagnostic, and a stream the bound cut is only trusted up to its last complete
token.
"""

from __future__ import annotations

import typing as typ

from .constants import LOG_BOUND_BYTES, VERSION_TOKEN
from .models import Ran

#: How a bounded stream marks the bytes it dropped. ``last_line`` ignores it,
#: so a truncated stream still names a line of its retained output.
TRUNCATION_PREFIX: typ.Final[str] = "[... "


def bounded(text: str) -> str:
    """Return at most ``LOG_BOUND_BYTES`` bytes of *text*, marking the rest.

    Parameters
    ----------
    text : str
        The stream to bound.

    Returns
    -------
    str
        *text* unchanged when it fits, else the kept prefix and a line naming
        how many bytes were dropped.

    """
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= LOG_BOUND_BYTES:
        return text
    kept = encoded[:LOG_BOUND_BYTES].decode("utf-8", "ignore")
    dropped = len(encoded) - LOG_BOUND_BYTES
    return f"{kept}\n{TRUNCATION_PREFIX}{dropped} bytes truncated]"


def complete_stream(text: str, *, truncated: bool) -> str:
    """Return *text* without the token a truncation may have cut in half.

    A token that ends at the bound is unproven: the next dropped byte could
    have been the digit that would have made it a different release. Complete
    tokens earlier in the stream are kept, so a version printed before a flood
    still counts; a token followed by whitespace is complete, because the
    dropped bytes began after the whitespace that ended it.

    Parameters
    ----------
    text : str
        One stream as the run recorded it.
    truncated : bool
        Whether the run dropped bytes from that stream. An untruncated stream
        is returned unchanged, so output that merely resembles the marker is
        not read as evidence that the stream was cut.

    Returns
    -------
    str
        *text* unchanged when the stream was complete, else its content up to
        the token the bound may have cut in half.

    """
    if not truncated:
        return text
    # The marker is the last line of a truncated stream and is written on its
    # own line, so the retained text ends where the marker's newline begins.
    head, _, _ = text.rpartition(f"\n{TRUNCATION_PREFIX}")
    trimmed = head.rstrip()
    if not trimmed:
        return ""
    if len(trimmed) < len(head):
        # Whitespace before the marker ends the last token, so the bound did
        # not cut it: what follows the whitespace was dropped after a complete
        # token, and the retained prefix is kept as printed.
        return head
    words = trimmed.split()
    return trimmed[: len(trimmed) - len(words[-1])]


def _diagnostic_lines(text: str) -> list[str]:
    """Return the meaningful lines of *text*, without the truncation marker.

    Leading whitespace is kept: it is what tells a compiler's first diagnostic
    apart from the traceback frames indented beneath it.
    """
    present = [line.rstrip() for line in text.splitlines() if line.strip()]
    return [line for line in present if not line.strip().startswith(TRUNCATION_PREFIX)]


def last_line(outcome: Ran) -> str:
    """Return the last non-empty line a command printed, for a one-line finding.

    Parameters
    ----------
    outcome : Ran
        The command's bounded result.

    Returns
    -------
    str
        The last non-empty line of standard error, else of standard output,
        else a phrase saying the command printed nothing.

    """
    for text in (outcome.stderr, outcome.stdout):
        printed = _diagnostic_lines(text)
        if printed:
            return printed[-1].strip()
    return "it printed nothing"


def first_diagnostic(outcome: Ran) -> str:
    """Return the first non-empty diagnostic line a command printed.

    A compiler reports the failure that stopped it on its first line and
    context afterwards, so a finding that quotes the last line quotes the
    least useful one.

    Parameters
    ----------
    outcome : Ran
        The command's bounded result.

    Returns
    -------
    str
        The first non-empty line of standard error, else of standard output,
        else a phrase saying the command printed nothing.

    """
    for text in (outcome.stderr, outcome.stdout):
        printed = _diagnostic_lines(text)
        if not printed:
            continue
        # A compiler's first diagnostic begins at the margin. A traceback's
        # first line is its header and its frames are indented, so the line
        # that names the failure is the first unindented one after them.
        leading = [
            line
            for line in printed
            if not line.startswith("Traceback") and not line[:1].isspace()
        ]
        return (leading or printed)[0].strip()
    return "it printed nothing"


def timeout_message(timeout: int) -> str:
    """Return why a command was stopped, in the units its declaration used.

    Parameters
    ----------
    timeout : int
        The ceiling the declaration set, in seconds.

    Returns
    -------
    str
        A phrase naming the ceiling, with the unit agreeing with it.

    """
    unit = "second" if timeout == 1 else "seconds"
    return f"it did not finish within {timeout} {unit}"


def reports_version(printed: str, wanted: str) -> bool:
    """Return whether *printed* reports *wanted* as a whole version token.

    A substring test would accept ``2.4`` for ``2.41.0``, which is the kind of
    near-miss this phase exists to catch, so the declared version must equal a
    dotted token in the output or be its dotted prefix: ``2.4`` accepts
    ``2.4.0`` and rejects ``2.41.0``. A pre- or post-release suffix stays in the
    token and makes it a different release, so none of ``1.2.3rc1``,
    ``1.2.3.post1`` or ``1.2.3-rc1`` reports the stable ``1.2.3``; a
    declaration that pins the suffixed release itself, such as ``0.9.0rc1``,
    still matches it exactly.

    Parameters
    ----------
    printed : str
        What the command wrote to both streams, joined.
    wanted : str
        The version the declaration requires.

    Returns
    -------
    bool
        Whether a token in *printed* equals *wanted* or extends it by dotted
        components.

    """
    for token in VERSION_TOKEN.findall(printed):
        if token == wanted:
            return True
        # A suffix makes the token a different release from the stable version
        # it resembles, whatever the dotted components in front of it say.
        if not all(part.isdigit() for part in token.split(".")):
            continue
        if token.startswith(f"{wanted}."):
            return True
    return False
