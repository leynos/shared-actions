"""Bounded metrics for the Dependabot auto-merge GitHub operations.

The auto-merge script reports what it decided: the audit outcome, the
page and commit counts, the foreign-commit notices. A maintainer can
read one run and learn why it did what it did. What no run says is how
long an operation took or how many attempts it needed, and those are
properties of the population rather than of any single run: how often
the merge-state refresh retries, how close the page ceiling comes, and
which call is slow when a job times out.

Spans are not the answer here. Nothing in this repository is traced,
and these scripts run once per workflow job and exit, so a span would
have nowhere to go. The convention this repository already uses is a
metric line on standard output, read back from the job log, as
`setup-rust` does for the sccache wrapper outcome.

Every value is drawn from a closed set or is a count. A raw duration, a
repository name or a pull-request number as a value would make each run
its own series, which is exactly what makes a rate uncountable. Nothing
here is given a token, a payload, a commit author or a raw error string.
"""

from __future__ import annotations

import contextlib
import enum
import time
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    import collections.abc as cabc

__all__ = [
    "LATENCY_BUCKETS",
    "METRIC_PREFIX",
    "SLOWEST_BUCKET",
    "Operation",
    "Outcome",
    "latency_bucket",
    "measured",
    "report",
]


class Operation(enum.StrEnum):
    """The GitHub calls this script makes, as a closed vocabulary.

    Named rather than derived from the GraphQL document, because a
    document is an unbounded label and because two of these share one
    code path and would otherwise be indistinguishable in the log.
    """

    LOOKUP = "lookup"
    REFRESH = "refresh"
    ENABLE = "enable"
    DISABLE = "disable"
    MERGE = "merge"


class Outcome(enum.StrEnum):
    """How one operation ended.

    Two values, not an error taxonomy. GitHub's failures reach this
    module as exceptions whose text is unbounded, and categorising them
    here would mean parsing that text. What a rate needs is the
    denominator and the numerator.
    """

    OK = "ok"
    FAILED = "failed"


#: Upper bounds and their labels. A duration is placed in one of these
#: rather than reported as itself. The boundaries sit around the
#: merge-state retry policy, whose default schedule puts an exhausted
#: refresh well past ten seconds.
LATENCY_BUCKETS: typ.Final = (
    (0.5, "under-500ms"),
    (2.0, "under-2s"),
    (10.0, "under-10s"),
    (60.0, "under-60s"),
)
#: What a duration past the last bucket is reported as.
SLOWEST_BUCKET: typ.Final = "over-60s"
#: The prefix every metric line carries, so a job log can be read for
#: them without matching the prose around them.
METRIC_PREFIX: typ.Final = "metric dependabot-automerge."


def latency_bucket(seconds: float) -> str:
    """Return the bounded label ``seconds`` is reported under.

    Parameters
    ----------
    seconds : float
        How long the operation took.

    Returns
    -------
    str
        One of the `LATENCY_BUCKETS` labels, or `SLOWEST_BUCKET`.

    Examples
    --------
    >>> latency_bucket(0.1)
    'under-500ms'
    >>> latency_bucket(30.0)
    'under-60s'
    >>> latency_bucket(120.0)
    'over-60s'
    """
    for bound, label in LATENCY_BUCKETS:
        if seconds < bound:
            return label
    return SLOWEST_BUCKET


def report(name: str, value: object) -> None:
    """Emit one metric line on standard output.

    Callers pass a bounded value. Nothing here makes one bounded.

    Parameters
    ----------
    name : str
        The metric's name, under the `dependabot-automerge` namespace.
    value : object
        Its bounded value.
    """
    print(f"{METRIC_PREFIX}{name}={value}")


@contextlib.contextmanager
def measured(
    operation: Operation,
    *,
    attempts: cabc.Callable[[], int] | None = None,
    clock: cabc.Callable[[], float] = time.monotonic,
) -> cabc.Iterator[None]:
    """Report how one GitHub operation ended, however it ended.

    Reported from a `finally`, so a failure is counted beside the
    successes. A retry rate read only from the runs that worked says
    nothing about the failures the retry did not prevent, which are the
    ones worth knowing about.

    Parameters
    ----------
    operation : Operation
        Which call this is.
    attempts : callable or None
        Read after the body, for an operation that retries. None
        reports a single attempt, which is what a call that cannot
        retry made.
    clock : callable
        Where the duration comes from. Injected so a latency bucket can
        be asserted rather than waited for. It reads a monotonic
        counter, only ever subtracted from another reading of itself.

    Yields
    ------
    None
        The body runs inside the measurement.
    """
    started = clock()
    outcome = Outcome.FAILED
    try:
        yield
        outcome = Outcome.OK
    finally:
        report(f"{operation}.outcome", outcome)
        report(f"{operation}.attempts", attempts() if attempts else 1)
        report(f"{operation}.latency", latency_bucket(clock() - started))
