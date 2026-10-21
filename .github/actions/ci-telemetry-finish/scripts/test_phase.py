"""The test phase's sccache figures and the job's threshold breaches.

A job that calls ``sccache-report`` twice (once after the build, once at the
end) gets the test phase's hit rate as the difference between the two
snapshots: sccache's counters are cumulative from the server's start, so the
build's work is subtracted rather than guessed. The module also judges the
record against thresholds and names the ones breached. Both outputs are
advisory: a breach is a ``::warning::`` annotation and a record field, never a
failed job.

All six fields are additive to schema 1 and null whenever they cannot be
established (no snapshot, no started server, a counter reset, thresholds not
applying), so a consumer that has never heard of them reads nothing different.
"""

from __future__ import annotations

import typing as typ

import sccache_stats

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
MODES = frozenset({"main-push", "always", "never"})
BREACH_HIT_RATIO = "test_hit_ratio"
BREACH_DISK = "min_free_disk"


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
    """
    values = {name: final[key] - build[key] for name, key in COUNTED}
    if any(value < 0 for value in values.values()):
        return _problem("counter-reset")
    attempts = values["sccache_test_hits"] + values["sccache_test_misses"]
    ratio = round(values["sccache_test_hits"] / attempts, 4) if attempts else None
    return {**null_fields(), **values, "sccache_test_hit_ratio": ratio}


def collect(
    env: typ.Mapping[str, str], sccache: typ.Mapping[str, typ.Any]
) -> dict[str, typ.Any]:
    """Return the test-phase fields; never raises.

    Null unless the job's sccache is ``started`` with counts and the build
    snapshot file (``CT_SCCACHE_BUILD_STATS_FILE``) exists. A snapshot that
    exists but cannot be used names its problem instead of falling back.
    """
    if (
        sccache.get("sccache_status") != "started"
        or sccache.get("sccache_hits") is None
    ):
        return null_fields()
    outcome = sccache_stats.read_stats_file(env.get("CT_SCCACHE_BUILD_STATS_FILE", ""))
    if outcome.kind == sccache_stats.ABSENT:
        return null_fields()
    if outcome.kind != sccache_stats.OK:
        return _problem(f"snapshot-{outcome.kind}")
    try:
        build = sccache_stats.normalize(outcome.document)
    except sccache_stats.StatsError:
        return _problem("snapshot-shape")
    return delta(sccache, build)


def _number(
    env: typ.Mapping[str, str],
    key: str,
    default: float,
    cast: typ.Callable[[str], float],
) -> float:
    """Return a numeric setting, or the default when it is unset or not a number."""
    try:
        return cast(env.get(key, ""))
    except ValueError:
        return default


def _on_default_branch(env: typ.Mapping[str, str]) -> bool:
    """Report whether the job is a push to the repository's default branch."""
    branch = env.get("CT_DEFAULT_BRANCH", "")
    on_branch = env.get("GITHUB_REF") == f"refs/heads/{branch}"
    return bool(branch) and env.get("GITHUB_EVENT_NAME") == "push" and on_branch


def applies(env: typ.Mapping[str, str]) -> bool:
    """Report whether thresholds apply: a push to the default branch, by default."""
    mode = env.get("CT_THRESHOLDS_MODE") or "main-push"
    if mode == "always":
        return True
    return mode == "main-push" and _on_default_branch(env)


def _ratio_min(env: typ.Mapping[str, str]) -> float:
    return _number(env, "CT_TEST_HIT_RATIO_MIN", DEFAULT_HIT_RATIO_MIN, float)


def _disk_min(env: typ.Mapping[str, str]) -> int:
    return int(_number(env, "CT_MIN_FREE_DISK_MIB", DEFAULT_MIN_FREE_DISK_MIB, int))


def _ratio_breached(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> bool:
    """Report a test-phase hit ratio below the minimum, over enough requests."""
    ratio = record.get("sccache_test_hit_ratio")
    requests = record.get("sccache_test_compile_requests")
    floor = _number(
        env, "CT_TEST_MIN_COMPILE_REQUESTS", DEFAULT_MIN_COMPILE_REQUESTS, int
    )
    judged = ratio is not None and requests is not None and requests >= floor
    return bool(judged and ratio < _ratio_min(env))


def _disk_breached(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> bool:
    """Report a least-free-disk figure below the minimum."""
    free = record.get("min_free_disk_mib")
    return free is not None and free < _disk_min(env)


def breaches(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> list[str] | None:
    """Return the breached thresholds, or None when thresholds do not apply."""
    if not applies(env):
        return None
    checks = ((BREACH_HIT_RATIO, _ratio_breached), (BREACH_DISK, _disk_breached))
    return [name for name, breached in checks if breached(record, env)]


def _ratio_warning(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> str:
    return (
        "::warning title=ci-telemetry-threshold::test-phase sccache hit ratio "
        f"{record.get('sccache_test_hit_ratio')} over "
        f"{record.get('sccache_test_compile_requests')} compile requests is "
        f"below {_ratio_min(env)}"
    )


def _disk_warning(record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]) -> str:
    return (
        "::warning title=ci-telemetry-threshold::least free disk "
        f"{record.get('min_free_disk_mib')} MiB is below {_disk_min(env)} MiB"
    )


def annotations(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> list[str]:
    """Return one ``::warning::`` line per breach recorded in *record*."""
    names = (record.get("threshold_breaches") or "").split(",")
    warnings = ((BREACH_HIT_RATIO, _ratio_warning), (BREACH_DISK, _disk_warning))
    return [warn(record, env) for name, warn in warnings if name in names]


def judge(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> dict[str, typ.Any]:
    """Return ``threshold_breaches``: null if not judged, else names or ``none``."""
    found = breaches(record, env)
    if found is None:
        return {"threshold_breaches": None}
    return {"threshold_breaches": ",".join(found) or "none"}
