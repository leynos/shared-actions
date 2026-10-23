"""Adapter between the environment and the pure test-phase policy.

``finish`` runs in a composite action whose inputs arrive as ``CT_*``
environment variables and whose job facts arrive as ``GITHUB_*`` ones. This
module is the only place those names are read for the thresholds, the only place
an sccache parse result becomes a policy snapshot, and the only place a policy
message becomes a GitHub workflow command. ``test_phase`` sees none of them.
"""

from __future__ import annotations

import math
import typing as typ

import sccache_stats
import test_phase

COMMAND = "::warning title=ci-telemetry-threshold::"


def _number(
    env: typ.Mapping[str, str],
    key: str,
    default: float,
    cast: typ.Callable[[str], float],
) -> float:
    """Return a numeric setting, or the default when unset, invalid or non-finite."""
    try:
        value = cast(env.get(key, ""))
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def thresholds_from_env(env: typ.Mapping[str, str]) -> test_phase.Thresholds:
    """Return the typed thresholds the action's inputs describe.

    Returns
    -------
    test_phase.Thresholds
        The limits and mode, defaults standing in for anything unusable.
    """
    return test_phase.Thresholds(
        hit_ratio_min=_number(
            env, "CT_TEST_HIT_RATIO_MIN", test_phase.DEFAULT_HIT_RATIO_MIN, float
        ),
        min_requests=int(
            _number(
                env,
                "CT_TEST_MIN_COMPILE_REQUESTS",
                test_phase.DEFAULT_MIN_COMPILE_REQUESTS,
                int,
            )
        ),
        min_free_disk_mib=int(
            _number(
                env,
                "CT_MIN_FREE_DISK_MIB",
                test_phase.DEFAULT_MIN_FREE_DISK_MIB,
                int,
            )
        ),
        mode=env.get("CT_THRESHOLDS_MODE") or test_phase.DEFAULT_MODE,
    )


def job_from_env(env: typ.Mapping[str, str]) -> test_phase.JobContext:
    """Return the job facts the policy needs.

    Returns
    -------
    test_phase.JobContext
        The event, ref and repository default branch.
    """
    return test_phase.JobContext(
        event=env.get("GITHUB_EVENT_NAME", ""),
        ref=env.get("GITHUB_REF", ""),
        default_branch=env.get("CT_DEFAULT_BRANCH", ""),
    )


def snapshot_from_outcome(outcome: sccache_stats.Outcome) -> test_phase.Snapshot:
    """Translate reading the build snapshot into what the policy takes.

    Returns
    -------
    test_phase.Snapshot
        None when no file exists, a problem name when it cannot be used, else
        its normalized counters.
    """
    if outcome.kind == sccache_stats.ABSENT:
        return None
    if outcome.kind != sccache_stats.OK:
        return f"snapshot-{outcome.kind}"
    try:
        return sccache_stats.normalize(outcome.document)
    except sccache_stats.StatsError:
        return "snapshot-shape"


def commands(messages: typ.Iterable[str]) -> list[str]:
    """Return each message as a GitHub ``::warning::`` workflow command.

    Returns
    -------
    list[str]
        One command line per message.
    """
    return [f"{COMMAND}{message}" for message in messages]
