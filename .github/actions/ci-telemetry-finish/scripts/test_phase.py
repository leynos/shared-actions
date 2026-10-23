"""The test phase's sccache figures and the job's threshold breaches (pure policy).

A job that calls ``sccache-report`` twice (once after the build, once at the
end) gets the test phase's hit rate as the difference between the two
snapshots: sccache's counters are cumulative from the server's start, so the
build's work is subtracted rather than guessed. The module also judges the
record against thresholds and names the ones breached. Both outputs are
advisory: a breach is a warning and a record field, never a failed job.

Everything here takes values: counters, a typed ``Thresholds`` and a typed
``JobContext``. Reading the environment, parsing sccache's files and writing
GitHub workflow commands belong to the ``threshold_settings`` adapter and to
``finish``, so the policy is tested with constructed values alone.

All six fields are additive to schema 1. The test-phase measurements and
``sccache_test_problem`` are null whenever the phase cannot be measured (no
snapshot, no started server); ``threshold_breaches`` is null whenever the
thresholds do not apply, whatever the measurements say. A consumer that has
never heard of the fields reads nothing different.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

FIELDS = (
    "sccache_test_hits",
    "sccache_test_misses",
    "sccache_test_compile_requests",
    "sccache_test_hit_ratio",
    "sccache_test_problem",
    "threshold_breaches",
)
COUNTED = (
    ("sccache_test_hits", "sccache_hits"),
    ("sccache_test_misses", "sccache_misses"),
    ("sccache_test_compile_requests", "sccache_compile_requests"),
)
DEFAULT_HIT_RATIO_MIN = 0.80
DEFAULT_MIN_COMPILE_REQUESTS = 20
DEFAULT_MIN_FREE_DISK_MIB = 3072
DEFAULT_MODE = "main-push"
MODES = frozenset({"main-push", "always", "never"})
BREACH_HIT_RATIO = "test_hit_ratio"
BREACH_DISK = "min_free_disk"

#: A post-build snapshot as the policy sees it: the cumulative counters, a
#: problem name (``snapshot-unreadable`` and the like), or None when none exists.
type Snapshot = typ.Mapping[str, int] | str | None


@dc.dataclass(frozen=True)
class Thresholds:
    """The tunable limits and where they apply."""

    hit_ratio_min: float = DEFAULT_HIT_RATIO_MIN
    min_requests: int = DEFAULT_MIN_COMPILE_REQUESTS
    min_free_disk_mib: int = DEFAULT_MIN_FREE_DISK_MIB
    mode: str = DEFAULT_MODE


@dc.dataclass(frozen=True)
class JobContext:
    """What the policy needs to know about the job: its event and branch."""

    event: str = ""
    ref: str = ""
    default_branch: str = ""


def null_fields() -> dict[str, typ.Any]:
    """Return every field of this module as null."""
    return dict.fromkeys(FIELDS)


def _problem(kind: str) -> dict[str, typ.Any]:
    return {**null_fields(), "sccache_test_problem": kind}


def delta(
    final: typ.Mapping[str, typ.Any], build: typ.Mapping[str, typ.Any]
) -> dict[str, typ.Any]:
    """Return the test-phase fields from two normalized sccache snapshots; pure.

    A counter that went down means the server restarted between the snapshots,
    so the difference means nothing and the fields say ``counter-reset``.

    Returns
    -------
    dict[str, Any]
        The test-phase fields.
    """
    values = {name: final[key] - build[key] for name, key in COUNTED}
    if any(value < 0 for value in values.values()):
        return _problem("counter-reset")
    attempts = values["sccache_test_hits"] + values["sccache_test_misses"]
    ratio = round(values["sccache_test_hits"] / attempts, 4) if attempts else None
    return {**null_fields(), **values, "sccache_test_hit_ratio": ratio}


def _has_counts(sccache: typ.Mapping[str, typ.Any]) -> bool:
    """Report a started server whose counters were read."""
    started = sccache.get("sccache_status") == "started"
    return started and sccache.get("sccache_hits") is not None


def collect(
    sccache: typ.Mapping[str, typ.Any], snapshot: Snapshot
) -> dict[str, typ.Any]:
    """Return the test-phase fields from the final figures and a snapshot.

    Null unless the job's sccache is ``started`` with counts and a snapshot
    exists. A snapshot that exists but cannot be used arrives as a problem name
    and is reported instead of falling back.

    Returns
    -------
    dict[str, Any]
        The test-phase fields.
    """
    if not _has_counts(sccache) or snapshot is None:
        return null_fields()
    if isinstance(snapshot, str):
        return _problem(snapshot)
    return delta(sccache, snapshot)


def _is_default_branch_push(job: JobContext) -> bool:
    """Report whether the job is a push to the repository's default branch."""
    on_branch = (
        job.default_branch != "" and job.ref == f"refs/heads/{job.default_branch}"
    )
    return job.event == "push" and on_branch


def applies(thresholds: Thresholds, job: JobContext) -> bool:
    """Report whether thresholds apply: a push to the default branch, by default.

    Returns
    -------
    bool
        True when the mode is ``always``, or ``main-push`` on such a push.
    """
    if thresholds.mode == "always":
        return True
    return thresholds.mode == "main-push" and _is_default_branch_push(job)


def _attempts(record: typ.Mapping[str, typ.Any]) -> tuple[int, int] | None:
    """Return ``(hits, attempts)`` for a measured phase with attempts, else None."""
    hits = record.get("sccache_test_hits")
    misses = record.get("sccache_test_misses")
    if None in (hits, misses):
        return None
    attempts = hits + misses
    return (hits, attempts) if attempts else None


def _enough_requests(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> bool:
    """Report a phase with at least the minimum number of compile requests."""
    requests = record.get("sccache_test_compile_requests")
    return requests is not None and requests >= thresholds.min_requests


def _ratio_breached(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> bool:
    """Report a test-phase hit ratio below the minimum, over enough requests.

    The exact quotient is judged, not the recorded four-place rounding:
    19,999 hits in 25,000 attempts records 0.8 and is still below 0.80.
    """
    counted = _attempts(record)
    if counted is None or not _enough_requests(record, thresholds):
        return False
    hits, attempts = counted
    return hits / attempts < thresholds.hit_ratio_min


def _disk_breached(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> bool:
    """Report a least-free-disk figure below the minimum."""
    free = record.get("min_free_disk_mib")
    return free is not None and free < thresholds.min_free_disk_mib


def breaches(
    record: typ.Mapping[str, typ.Any], thresholds: Thresholds, job: JobContext
) -> list[str] | None:
    """Return the breached thresholds, or None when thresholds do not apply.

    Returns
    -------
    list[str] | None
        Breach names in a fixed order, or None.
    """
    if not applies(thresholds, job):
        return None
    checks = ((BREACH_HIT_RATIO, _ratio_breached), (BREACH_DISK, _disk_breached))
    return [name for name, breached in checks if breached(record, thresholds)]


def judge(
    record: typ.Mapping[str, typ.Any], thresholds: Thresholds, job: JobContext
) -> dict[str, typ.Any]:
    """Return ``threshold_breaches``: null if not judged, else names or ``none``.

    Returns
    -------
    dict[str, Any]
        A one-key mapping for the record.
    """
    found = breaches(record, thresholds, job)
    if found is None:
        return {"threshold_breaches": None}
    return {"threshold_breaches": ",".join(found) or "none"}


def _ratio_message(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> str:
    counted = _attempts(record)
    seen = (
        f"{counted[0] / counted[1]:.5f} ({counted[0]} hits in {counted[1]} attempts"
        if counted
        else f"{record.get('sccache_test_hit_ratio')} (ratio recorded"
    )
    return (
        f"test-phase sccache hit ratio {seen}, "
        f"{record.get('sccache_test_compile_requests')} compile requests) is "
        f"below {thresholds.hit_ratio_min}"
    )


def _disk_message(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> str:
    return (
        f"least free disk {record.get('min_free_disk_mib')} MiB is below "
        f"{thresholds.min_free_disk_mib} MiB"
    )


def messages(record: typ.Mapping[str, typ.Any], thresholds: Thresholds) -> list[str]:
    """Return one plain-text message per breach recorded in *record*.

    An unknown ``mode`` adds one configuration message, since it would
    otherwise disable the check without a word. The adapter turns each message
    into a workflow command.

    Returns
    -------
    list[str]
        The messages, breaches first in their fixed order.
    """
    names = (record.get("threshold_breaches") or "").split(",")
    texts = ((BREACH_HIT_RATIO, _ratio_message), (BREACH_DISK, _disk_message))
    found = [text(record, thresholds) for name, text in texts if name in names]
    if thresholds.mode not in MODES:
        found.append(
            f"unknown thresholds mode {thresholds.mode!r}; expected main-push, "
            "always or never, so no threshold is judged"
        )
    return found
