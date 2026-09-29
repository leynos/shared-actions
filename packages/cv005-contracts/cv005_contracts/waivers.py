"""Apply a repository's declared exceptions to the findings of a run.

An exception waives one clause on the record. It never passes silently: the
waived findings are returned so the command prints them, and the exception
itself is a finding when it names no clause, waives nothing, or claims a
replacement the publisher does not run.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from .reading import jobs, steps

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .declared import Exemption
    from .loading import Document


@dc.dataclass(frozen=True, slots=True)
class Waiver:
    """One exception and the findings it waived in this run.

    Attributes
    ----------
    exemption : Exemption
        The declared exception.
    findings : tuple[str, ...]
        What the waived clause found; never empty, since an exception that
        waives nothing is refused as stale.

    """

    exemption: Exemption
    findings: tuple[str, ...]


def apply_exceptions(
    results: cabc.Mapping[str, list[str]],
    known: cabc.Collection[str],
    exceptions: cabc.Sequence[Exemption],
    publisher: Document,
) -> tuple[list[tuple[str, str]], list[Waiver]]:
    """Return the findings that stand, and the ones the exceptions waived.

    Parameters
    ----------
    results : Mapping[str, list[str]]
        The findings of each clause that ran, by clause identifier.
    known : Collection[str]
        Every clause identifier, whether or not it ran.
    exceptions : Sequence[Exemption]
        The declared exceptions.
    publisher : Document
        The publisher workflow, in which a `requires` text must be run.

    Returns
    -------
    tuple[list[tuple[str, str]], list[Waiver]]
        The standing findings as `(clause, message)`, including any finding
        about an exception itself, and the waivers.

    """
    waived = {exemption.clause for exemption in exceptions}
    standing = [
        (clause, message)
        for clause, messages in results.items()
        if clause not in waived
        for message in messages
    ]
    waivers: list[Waiver] = []
    for exemption in exceptions:
        problems, waiver = _judge(exemption, results, known, publisher)
        standing += [("exception", problem) for problem in problems]
        waivers += [waiver] if waiver else []
    return standing, waivers


def _judge(
    exemption: Exemption,
    results: cabc.Mapping[str, list[str]],
    known: cabc.Collection[str],
    publisher: Document,
) -> tuple[list[str], Waiver | None]:
    """Judge one exception: what it says about itself, and what it waived."""
    problems: list[str] = []
    waiver: Waiver | None = None
    if exemption.clause not in known:
        problems.append(f"{exemption.clause} is not a clause; it cannot be waived")
    elif exemption.clause in results:
        if findings := results[exemption.clause]:
            waiver = Waiver(exemption, tuple(findings))
        else:
            problems.append(
                f"the exception for {exemption.clause} waives nothing; remove it"
            )
    if exemption.requires and not _runs(publisher, exemption.requires):
        problems.append(
            f"the exception for {exemption.clause} needs the publisher to run "
            f"`{exemption.requires}`, and no step does"
        )
    return problems, waiver


def _runs(publisher: Document, command: str) -> bool:
    """Return whether some step of the publisher's `run` contains `command`."""
    return any(
        command in str(step.get("run", ""))
        for job in jobs(publisher).values()
        for step in steps(job)
    )
