"""GitHub merge state, and what each combination means for auto-merge.

Four outcomes, not two. A pull request can be armed for auto-merge,
merged outright because GitHub refuses to arm an already-mergeable one,
skipped, or retried because GitHub has not finished computing
mergeability. Keeping that classification here, away from the decision
flow, is what lets it be read and tested as a table.
"""

from __future__ import annotations

import dataclasses
import enum
import math
import os
import typing as typ
from types import MappingProxyType

if __package__:
    from .output import fail
else:
    from output import fail  # type: ignore[import-not-found,no-redef]

MergeStateClassification = typ.Literal["ok", "merge", "skip", "retry"]


class MergeStateStatus(enum.StrEnum):
    """Supported merge state statuses from GitHub GraphQL."""

    BEHIND = "BEHIND"
    BLOCKED = "BLOCKED"
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    DRAFT = "DRAFT"
    HAS_HOOKS = "HAS_HOOKS"
    MERGED = "MERGED"
    UNKNOWN = "UNKNOWN"
    UNSTABLE = "UNSTABLE"


class MergeableState(enum.StrEnum):
    """Supported mergeable states from GitHub GraphQL."""

    CONFLICTING = "CONFLICTING"
    MERGEABLE = "MERGEABLE"
    UNKNOWN = "UNKNOWN"


MERGE_STATE_SKIP_REASONS: typ.Mapping[MergeStateStatus, str] = MappingProxyType(
    {
        MergeStateStatus.DIRTY: "merge-state-dirty",
        MergeStateStatus.BEHIND: "merge-state-behind",
        MergeStateStatus.MERGED: "already-merged",
    }
)
# States where the PR is already mergeable. GitHub rejects
# enablePullRequestAutoMerge here ("Pull request is in clean/unstable
# status"), so the PR is merged directly instead — mirroring what
# auto-merge would do, since all *required* rules are already satisfied.
MERGE_STATE_DIRECT_MERGE: frozenset[MergeStateStatus] = frozenset(
    {
        MergeStateStatus.CLEAN,
        MergeStateStatus.HAS_HOOKS,
        MergeStateStatus.UNSTABLE,
    }
)
MERGEABLE_SKIP_REASONS: typ.Mapping[MergeableState, str] = MappingProxyType(
    {
        MergeableState.CONFLICTING: "mergeable-conflicting",
    }
)
MERGE_STATE_RETRYABLE: frozenset[MergeStateStatus] = frozenset(
    {MergeStateStatus.UNKNOWN}
)
MERGEABLE_RETRYABLE: frozenset[MergeableState] = frozenset({MergeableState.UNKNOWN})
MERGE_STATE_MAX_ATTEMPTS_DEFAULT: int = 3
MERGE_STATE_BASE_SLEEP_DEFAULT: float = 2.0
MERGE_STATE_MAX_SLEEP_DEFAULT: float = 30.0
MERGE_STATE_MAX_ATTEMPTS_ENV: str = "AUTOMERGE_MERGE_STATE_MAX_ATTEMPTS"
MERGE_STATE_BASE_SLEEP_ENV: str = "AUTOMERGE_MERGE_STATE_BASE_SLEEP_SECONDS"
MERGE_STATE_MAX_SLEEP_ENV: str = "AUTOMERGE_MERGE_STATE_MAX_SLEEP_SECONDS"

type MergeStateClassification = typ.Literal["ok", "merge", "skip", "retry"]


def classify_merge_state(
    merge_state: MergeStateStatus, mergeable_state: MergeableState
) -> tuple[MergeStateClassification, str | None]:
    """Classify merge state as ok, merge, skip, or retry with a reason.

    ``ok`` means auto-merge can be armed (notably ``BLOCKED``, where required
    checks are still pending). ``merge`` means the PR is already mergeable, so
    it must be merged directly because GitHub rejects
    ``enablePullRequestAutoMerge`` on an already-mergeable pull request.
    """
    if mergeable_state in MERGEABLE_SKIP_REASONS:
        return "skip", MERGEABLE_SKIP_REASONS[mergeable_state]
    if merge_state in MERGE_STATE_SKIP_REASONS:
        return "skip", MERGE_STATE_SKIP_REASONS[merge_state]
    if merge_state in MERGE_STATE_DIRECT_MERGE:
        return "merge", "already-mergeable"
    if merge_state in MERGE_STATE_RETRYABLE or mergeable_state in MERGEABLE_RETRYABLE:
        return "retry", "merge-state-unknown"
    return "ok", None


def _parse_env_int(name: str, default: int) -> int:
    """Parse an integer from the environment, or return the default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        fail(f"Invalid value for {name}: {value!r}. Expected an integer.")
    if parsed < 0:
        fail(f"Invalid value for {name}: {value!r}. Expected a non-negative integer.")
    return parsed


def _parse_env_float(name: str, default: float) -> float:
    """Parse a float from the environment, or return the default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        fail(f"Invalid value for {name}: {value!r}. Expected a number.")
    if not math.isfinite(parsed):
        fail(f"Invalid value for {name}: {value!r}. Expected a finite number.")
    if parsed < 0:
        fail(f"Invalid value for {name}: {value!r}. Expected a non-negative number.")
    return parsed


@dataclasses.dataclass(frozen=True, slots=True)
class MergeStateRetryConfig:
    """Configuration for merge state refresh retry behaviour."""

    max_attempts: int
    base_sleep: float
    max_sleep: float


def merge_state_retry_config() -> MergeStateRetryConfig:
    """Return retry configuration for merge state refresh."""
    max_attempts = _parse_env_int(
        MERGE_STATE_MAX_ATTEMPTS_ENV, MERGE_STATE_MAX_ATTEMPTS_DEFAULT
    )
    base_sleep = _parse_env_float(
        MERGE_STATE_BASE_SLEEP_ENV, MERGE_STATE_BASE_SLEEP_DEFAULT
    )
    max_sleep = _parse_env_float(
        MERGE_STATE_MAX_SLEEP_ENV, MERGE_STATE_MAX_SLEEP_DEFAULT
    )
    return MergeStateRetryConfig(
        max_attempts=max_attempts,
        base_sleep=base_sleep,
        max_sleep=max_sleep,
    )
