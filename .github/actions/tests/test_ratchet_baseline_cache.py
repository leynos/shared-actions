"""Cross-action contract for the coverage ratchet baseline cache.

Two actions persist a ratchet baseline between runs, and both must do it the
same way. GitHub Actions cache entries are immutable, so a constant key can be
written once and then freezes the baseline until eviction, which makes later
runs false-trip "coverage decreased". The full ``actions/cache`` action also
registers a post-job save of its own, so pairing it with an explicit save step
gives one key two writers and the second loses the reservation.

The shape that avoids both: restore through ``actions/cache/restore`` on a
run-scoped primary key with a shared prefix as its restore-key, then save
through ``actions/cache/save`` under that same run-scoped key.

Action-specific behaviour, such as the reporting step ``generate-coverage``
emits, is covered in that action's own test directory.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

ACTIONS_ROOT = Path(__file__).resolve().parents[1]

#: Each action that caches a ratchet baseline, mapped to the names of its
#: restore and save steps.
BASELINE_CACHE_STEPS = {
    "generate-coverage": ("Restore baselines", "Save baselines"),
    "ratchet-coverage": ("Restore baseline", "Save baseline"),
}

#: The prefix both halves must share, before the run-scoped suffix.
KEY_PREFIX = "ratchet-baseline-${{ runner.os }}-"
RUN_SCOPE = "${{ github.run_id }}"

#: How each cache action variant participates in the baseline lifecycle,
#: as (reads, writes).
CACHE_VARIANT_ROLES = {
    "actions/cache": (1, 1),
    "actions/cache/restore": (1, 0),
    "actions/cache/save": (0, 1),
}

_REFERENCE = re.compile(r"^(?P<action>[^@]+)@(?P<sha>[0-9a-f]{40})$")

ACTION_IDS = sorted(BASELINE_CACHE_STEPS)


def _steps(action: str) -> list[dict[str, typ.Any]]:
    """Return the composite step definitions for *action*."""
    manifest = ACTIONS_ROOT / action / "action.yml"
    loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    return loaded["runs"]["steps"]


def _step(action: str, name: str) -> dict[str, typ.Any]:
    """Return the single step of *action* named *name*."""
    matches = [step for step in _steps(action) if step.get("name") == name]
    assert len(matches) == 1, f"{action}: expected exactly one {name!r} step"
    return matches[0]


def _reference(action: str, step_name: str) -> re.Match[str]:
    """Return the parsed ``uses`` reference for a baseline cache step."""
    uses = _step(action, step_name)["uses"]
    match = _REFERENCE.fullmatch(uses)
    assert match is not None, f"{action}: {step_name!r} is not SHA-pinned: {uses}"
    return match


def _lifecycle_is_sound(first_variant: str, second_variant: str) -> bool:
    """Return whether a pairing reads once, then writes once, in that order.

    Anything else either gives the key two writers, which loses the
    reservation, or restores a second time after the ratchet has already
    advanced the baseline file.
    """
    return CACHE_VARIANT_ROLES[first_variant] == (1, 0) and CACHE_VARIANT_ROLES[
        second_variant
    ] == (0, 1)


@given(
    first_variant=st.sampled_from(sorted(CACHE_VARIANT_ROLES)),
    second_variant=st.sampled_from(sorted(CACHE_VARIANT_ROLES)),
)
@settings(max_examples=25, derandomize=True, deadline=None)
def test_only_the_split_pairing_is_a_sound_lifecycle(
    first_variant: str, second_variant: str
) -> None:
    """Single out the restore/save pair among every variant combination."""
    is_split = (first_variant, second_variant) == (
        "actions/cache/restore",
        "actions/cache/save",
    )

    assert _lifecycle_is_sound(first_variant, second_variant) is is_split


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_pair_uses_the_split_subactions(action: str) -> None:
    """Exactly one step may write the baseline key."""
    restore_step, save_step = BASELINE_CACHE_STEPS[action]

    assert _lifecycle_is_sound(
        _reference(action, restore_step)["action"],
        _reference(action, save_step)["action"],
    )


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_pair_shares_one_pinned_revision(action: str) -> None:
    """Both halves must come from the same pinned release."""
    restore_step, save_step = BASELINE_CACHE_STEPS[action]
    revisions = {
        _reference(action, restore_step)["sha"],
        _reference(action, save_step)["sha"],
    }

    assert len(revisions) == 1, f"{action}: halves pin differing revisions"


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_key_is_run_scoped_and_shared(action: str) -> None:
    """A constant key freezes the baseline after its first write."""
    restore_step, save_step = BASELINE_CACHE_STEPS[action]
    restore_key = _step(action, restore_step)["with"]["key"]
    save_key = _step(action, save_step)["with"]["key"]

    assert restore_key.startswith(KEY_PREFIX)
    assert RUN_SCOPE in restore_key, f"{action}: restore key is not run-scoped"
    assert save_key == restore_key, f"{action}: halves disagree on the key"


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_restore_falls_back_to_the_shared_prefix(action: str) -> None:
    """A run-scoped key only works with a prefix to recover the newest entry."""
    restore_step, _save_step = BASELINE_CACHE_STEPS[action]
    restore_keys = _step(action, restore_step)["with"]["restore-keys"]

    assert restore_keys.strip() == KEY_PREFIX


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_restore_precedes_save_over_matching_paths(action: str) -> None:
    """The pair must read before it writes, over exactly the same files."""
    restore_step, save_step = BASELINE_CACHE_STEPS[action]
    names = [step.get("name") for step in _steps(action)]
    assert names.index(restore_step) < names.index(save_step)

    assert (
        _step(action, restore_step)["with"]["path"]
        == (_step(action, save_step)["with"]["path"])
    )


@pytest.mark.parametrize("action", ACTION_IDS)
def test_baseline_save_is_not_gated_on_cache_hit(action: str) -> None:
    """A ``cache-hit`` guard would suppress the advanced baseline.

    The restore primary key never matches within the same run, so the guard
    could only ever prevent the write.
    """
    _restore_step, save_step = BASELINE_CACHE_STEPS[action]
    condition = str(_step(action, save_step).get("if", ""))

    assert "cache-hit" not in condition
