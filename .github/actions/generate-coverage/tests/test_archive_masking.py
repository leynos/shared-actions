"""Contract tests for the coverage archive-masking guard.

The "Archive coverage" step runs with ``if: always()`` so a failing run still
uploads its coverage report. Its artefact name comes from the ``out`` step,
which historically ran without ``always()`` and so was skipped whenever an
earlier step failed. The empty name then failed the always-run upload with a
confusing "artifact name input is empty" error that masked the real cause (for
example a tripped ratchet gate).

These tests lock in the fix:

* ``out`` runs after a *later* failure (``always()``) but is gated on a
  successful detection (``steps.detect.outputs.fmt != ''``), so a failed
  ``detect`` does not launch ``set_outputs.py`` with an empty format and raise a
  second failure of the same kind.
* the archive step always runs and its ``name`` has a fallback for the case
  where ``out`` was skipped, so the upload never fails on an empty name.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_YML = ACTION_DIR / "action.yml"


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    data = yaml.safe_load(ACTION_YML.read_text())
    return data["runs"]["steps"]


def _step_by_id(step_id: str) -> dict[str, typ.Any]:
    """Return the single step whose ``id`` matches ``step_id``."""
    matches = [step for step in _steps() if step.get("id") == step_id]
    assert len(matches) == 1, (
        f"expected exactly one {step_id!r} step, got {len(matches)}"
    )
    return matches[0]


def _step_by_name(name: str) -> dict[str, typ.Any]:
    """Return the single step whose ``name`` matches ``name``."""
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def test_out_step_runs_after_later_failures() -> None:
    """``out`` must run even after a later step (e.g. the ratchet) fails.

    Without ``always()`` the step is skipped on failure, leaving the artefact
    name empty and masking the real cause with an empty-name upload error.
    """
    condition = str(_step_by_id("out")["if"])
    assert "always()" in condition, (
        "out is not guarded by always(); a ratchet failure will skip it and "
        "mask the real error with an empty-artefact-name upload failure"
    )


def test_out_step_is_gated_on_successful_detection() -> None:
    """``out`` must skip when detection produced no format.

    A failed ``detect`` writes no ``fmt`` output; running ``set_outputs.py``
    there raises "Coverage format is required", a second failure of the same
    kind this guard removes.
    """
    condition = str(_step_by_id("out")["if"])
    assert "steps.detect.outputs.fmt" in condition, (
        "out is not gated on a successful detection; it will re-raise a format "
        "error when detect itself failed"
    )


def test_archive_step_always_runs_with_name_fallback() -> None:
    """The archive step always runs and never uploads with an empty name."""
    archive = _step_by_name("Archive coverage")
    assert "always()" in str(archive["if"])
    name = archive["with"]["name"]
    assert "steps.out.outputs.artefact_name" in name
    assert "||" in name, (
        "archive name has no fallback; a skipped out step yields an empty name "
        "and fails the upload"
    )
