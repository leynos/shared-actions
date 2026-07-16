"""Tests for the coverage ratchet baseline contract.

Two properties are exercised here:

* The ``action.yml`` cache save/restore key contract. GitHub Actions cache
  entries are immutable, so a constant save key freezes the ratchet baseline at
  whatever the first post-eviction run measured. The save key must therefore
  vary per run and match the restore step's run-id-suffixed primary key, and
  the save step must not be gated on ``cache-hit`` (which would suppress the
  write once a constant key existed). This is a regression guard for the
  baseline-freeze bug that caused downstream repositories to false-trip
  "Coverage decreased" on pull requests.
* The ``ratchet_coverage.py`` script's baseline-advance semantics: the stored
  baseline rises when coverage improves, holds when coverage is unchanged, and
  the gate fails when coverage drops below the baseline.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest
import typer
import yaml

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_YML = ACTION_DIR / "action.yml"


def _load_ratchet_module() -> ModuleType:
    """Import ``ratchet_coverage`` from the action's ``scripts`` directory."""
    script = ACTION_DIR / "scripts" / "ratchet_coverage.py"
    sys.modules.pop("ratchet_coverage", None)
    spec = importlib.util.spec_from_file_location("ratchet_coverage", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    data = yaml.safe_load(ACTION_YML.read_text())
    return data["runs"]["steps"]


def _step_by_name(name: str) -> dict[str, typ.Any]:
    """Return the single step whose ``name`` matches ``name``."""
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def test_restore_baselines_uses_run_id_primary_key_and_prefix() -> None:
    """The restore step keys on the run id with a shared prefix restore-key."""
    restore = _step_by_name("Restore baselines")
    key = restore["with"]["key"]
    restore_keys = restore["with"]["restore-keys"]
    assert "${{ github.run_id }}" in key
    assert key.startswith("ratchet-baseline-${{ runner.os }}-")
    # The restore-key prefix must match the per-run save key so the newest
    # baseline is recovered on subsequent runs.
    assert restore_keys.strip() == "ratchet-baseline-${{ runner.os }}-"


def test_save_baselines_key_varies_per_run() -> None:
    """The save key must include the run id so a fresh baseline is written.

    A constant key such as ``ratchet-baseline-${{ runner.os }}`` is immutable
    after its first write and freezes the ratchet. The save key must instead
    match the restore step's run-id-suffixed primary key.
    """
    save = _step_by_name("Save baselines")
    key = save["with"]["key"]
    assert "${{ github.run_id }}" in key, (
        "save key is constant; the baseline will freeze after the first write"
    )
    assert key == _step_by_name("Restore baselines")["with"]["key"]


def test_save_baselines_not_gated_on_cache_hit() -> None:
    """The save step must not be suppressed by a ``cache-hit`` guard.

    The historical ``cache-hit != 'true'`` guard, combined with the constant
    save key, meant the improved baseline was never persisted.
    """
    save = _step_by_name("Save baselines")
    condition = save["if"]
    assert "cache-hit" not in condition, (
        "cache-hit guard reintroduced; the advanced baseline will not persist"
    )
    assert "inputs.with-ratchet == 'true'" in condition
    assert "success()" in condition


def test_tolerance_constant_is_one_percentage_point() -> None:
    """The provisional tolerance band is one absolute percentage point."""
    module = _load_ratchet_module()
    assert module.RATCHET_TOLERANCE_PP == 1.0


def test_baseline_advances_when_coverage_rises(tmp_path: Path) -> None:
    """A higher current percentage overwrites the stored baseline."""
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("85.20")

    module.main(baseline_file=baseline, current=90.05)

    assert baseline.read_text() == "90.05"


def test_exactly_equal_passes_and_holds(tmp_path: Path) -> None:
    """An equal current percentage keeps the baseline and does not fail."""
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.python"
    baseline.write_text("85.23")

    module.main(baseline_file=baseline, current=85.23)

    assert baseline.read_text() == "85.23"


def test_within_tolerance_dip_passes_without_lowering_baseline(
    tmp_path: Path,
) -> None:
    """A dip inside the tolerance band passes but must not lower the baseline.

    This is the chutoro scenario: 85.20% on a pull request against an 85.23%
    baseline is a 0.03pp dip, well within the 1.0pp band. It must pass and
    leave the baseline unchanged so the band cannot erode it downwards.
    """
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("85.23")

    module.main(baseline_file=baseline, current=85.20)

    assert baseline.read_text() == "85.23"


def test_dip_at_tolerance_edge_passes_and_holds(tmp_path: Path) -> None:
    """A drop of exactly the tolerance band passes and holds the baseline."""
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("90.00")

    module.main(baseline_file=baseline, current=89.00)

    assert baseline.read_text() == "90.00"


def test_within_tolerance_rise_passes_without_raising_baseline(
    tmp_path: Path,
) -> None:
    """A rise inside the band passes but must not inflate the baseline.

    A lucky-high run within +/- the band must not raise the baseline, otherwise
    the next normal run could fall outside the band and fail.
    """
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("85.00")

    module.main(baseline_file=baseline, current=85.50)

    assert baseline.read_text() == "85.00"


def test_rise_at_tolerance_edge_holds_baseline(tmp_path: Path) -> None:
    """A rise of exactly the tolerance band holds the baseline.

    Only a strictly greater improvement advances it.
    """
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("85.00")

    module.main(baseline_file=baseline, current=86.00)

    assert baseline.read_text() == "85.00"


def test_gate_fails_when_coverage_drops_beyond_tolerance(tmp_path: Path) -> None:
    """A drop beyond the tolerance band fails and leaves the baseline intact."""
    module = _load_ratchet_module()
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("85.23")

    with pytest.raises(typer.Exit) as excinfo:
        module.main(baseline_file=baseline, current=84.00)

    assert excinfo.value.exit_code == 1
    assert baseline.read_text() == "85.23"


def test_missing_baseline_is_treated_as_zero(tmp_path: Path) -> None:
    """A first run with no stored baseline records the current percentage."""
    module = _load_ratchet_module()
    baseline = tmp_path / "nested" / ".coverage-baseline.rust"

    module.main(baseline_file=baseline, current=42.5)

    assert baseline.read_text() == "42.50"
