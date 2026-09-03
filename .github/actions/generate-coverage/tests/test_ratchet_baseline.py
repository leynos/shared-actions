"""Tests for this action's ratchet baseline reporting and gate.

Two things are exercised here:

* The reporting step's bounded outcomes, in the log notice, the job summary,
  and the two stable metric lines, together with the manifest wiring that
  feeds it: its position after the save, its ``always()`` condition, and the
  step outputs each reported value comes from.
* The ``ratchet_coverage.py`` script's baseline-advance semantics: the stored
  baseline rises when coverage improves, holds when coverage is unchanged, and
  the gate fails when coverage drops below the baseline.

The cache lifecycle itself, the restore/save split, the run-scoped key, the
shared prefix and the matching paths, is a contract shared with
``ratchet-coverage`` and lives in
``.github/actions/tests/test_ratchet_baseline_cache.py``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
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


def _ratchet_report_script() -> str:
    """Return the ratchet cache reporting shell fragment."""
    script = _step_by_name("Report ratchet baseline cache decisions").get("run")
    assert isinstance(script, str), "ratchet cache reporter must be a script"
    return script


def _run_ratchet_report(
    *, summary_path: Path | None = None, **environment: str
) -> subprocess.CompletedProcess[str]:
    """Run the ratchet cache reporter with explicit step observations.

    ``summary_path`` supplies a ``GITHUB_STEP_SUMMARY`` destination; when it is
    omitted the variable is removed, so the notice path is exercised alone.
    """
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    process_env = {**os.environ, **environment}
    if summary_path is None:
        process_env.pop("GITHUB_STEP_SUMMARY", None)
    else:
        process_env["GITHUB_STEP_SUMMARY"] = str(summary_path)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", _ratchet_report_script()],
        env=process_env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("environment", "expected_notice"),
    [
        (
            {
                "GC_WITH_RATCHET": "false",
                "GC_RESTORE_STEP_OUTCOME": "skipped",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            "restore=disabled save=disabled",
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "success",
                "GC_RESTORE_CACHE_HIT": "true",
                "GC_SAVE_STEP_OUTCOME": "success",
            },
            "restore=hit save=saved",
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "success",
                "GC_RESTORE_CACHE_HIT": "false",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            "restore=miss save=skipped",
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "failure",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "failure",
            },
            "restore=error save=error",
        ),
        (
            # An earlier failure skips the restore step through the implicit
            # success() in its condition, while the cache remains enabled.
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "skipped",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            "restore=skipped save=skipped",
        ),
    ],
)
def test_ratchet_report_uses_bounded_outcomes(
    environment: dict[str, str], expected_notice: str
) -> None:
    """Report each half of the split with a closed set of outcomes."""
    result = _run_ratchet_report(**environment)

    assert result.returncode == 0, result.stderr
    assert expected_notice in result.stdout


#: Metric names the reporter emits, mapped to the step each describes. The
#: names are fixed and the values come from closed vocabularies, so a scraper
#: sees bounded cardinality.
RATCHET_METRIC_NAMES = {
    "ratchet-cache.restore": "GC_RESTORE_STEP_OUTCOME",
    "ratchet-cache.save": "GC_SAVE_STEP_OUTCOME",
}

#: Every value each metric may take. Adding a state here without adding it to
#: the reporter, or the reverse, breaks the closed-vocabulary guarantee.
RESTORE_STATES = frozenset({"hit", "miss", "skipped", "disabled", "error"})
SAVE_STATES = frozenset({"saved", "skipped", "disabled", "error"})


@pytest.mark.parametrize(
    ("environment", "expected_metrics"),
    [
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "success",
                "GC_RESTORE_CACHE_HIT": "true",
                "GC_SAVE_STEP_OUTCOME": "success",
            },
            ("ratchet-cache.restore=hit", "ratchet-cache.save=saved"),
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "success",
                "GC_RESTORE_CACHE_HIT": "false",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            ("ratchet-cache.restore=miss", "ratchet-cache.save=skipped"),
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "skipped",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            ("ratchet-cache.restore=skipped", "ratchet-cache.save=skipped"),
        ),
        (
            {
                "GC_WITH_RATCHET": "true",
                "GC_RESTORE_STEP_OUTCOME": "failure",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "failure",
            },
            ("ratchet-cache.restore=error", "ratchet-cache.save=error"),
        ),
        (
            {
                "GC_WITH_RATCHET": "false",
                "GC_RESTORE_STEP_OUTCOME": "skipped",
                "GC_RESTORE_CACHE_HIT": "",
                "GC_SAVE_STEP_OUTCOME": "skipped",
            },
            ("ratchet-cache.restore=disabled", "ratchet-cache.save=disabled"),
        ),
    ],
)
def test_ratchet_report_emits_one_metric_per_outcome(
    environment: dict[str, str], expected_metrics: tuple[str, str]
) -> None:
    """Each half must produce one stable metric line a scraper can match.

    The notice is written for a human reading the log; these lines are the
    machine-readable form, so their shape must not drift with the prose.
    """
    result = _run_ratchet_report(**environment)

    assert result.returncode == 0, result.stderr
    for metric in expected_metrics:
        assert f"metric {metric}" in result.stdout


def test_metric_values_stay_inside_the_closed_vocabularies() -> None:
    """No observation may produce a state outside the documented sets.

    An unbounded value would make the metric useless to aggregate, which is
    the whole reason the outcome vocabularies are closed.
    """
    observations = [
        ("true", outcome, cache_hit, save)
        for outcome, cache_hit in (
            ("success", "true"),
            ("success", "false"),
            ("success", ""),
            ("success", "unexpected"),
            ("skipped", ""),
            ("failure", ""),
            ("cancelled", ""),
        )
        for save in ("success", "skipped", "failure", "cancelled")
    ]
    observations.append(("false", "skipped", "", "skipped"))

    for with_ratchet, restore_outcome, cache_hit, save_outcome in observations:
        result = _run_ratchet_report(
            GC_WITH_RATCHET=with_ratchet,
            GC_RESTORE_STEP_OUTCOME=restore_outcome,
            GC_RESTORE_CACHE_HIT=cache_hit,
            GC_SAVE_STEP_OUTCOME=save_outcome,
        )
        assert result.returncode == 0, result.stderr
        emitted = dict(
            line.removeprefix("metric ").split("=", 1)
            for line in result.stdout.splitlines()
            if line.startswith("metric ")
        )
        assert set(emitted) == set(RATCHET_METRIC_NAMES)
        assert emitted["ratchet-cache.restore"] in RESTORE_STATES
        assert emitted["ratchet-cache.save"] in SAVE_STATES


def test_metric_lines_carry_no_cache_identifiers() -> None:
    """The metric must be as redacted as the notice it accompanies."""
    result = _run_ratchet_report(
        GC_WITH_RATCHET="true",
        GC_RESTORE_STEP_OUTCOME="success",
        GC_RESTORE_CACHE_HIT="true",
        GC_SAVE_STEP_OUTCOME="success",
    )

    metric_lines = [
        line for line in result.stdout.splitlines() if line.startswith("metric ")
    ]
    assert metric_lines
    for line in metric_lines:
        assert "ratchet-baseline-" not in line
        assert "coverage-baseline" not in line


def test_ratchet_report_never_names_the_cache_key() -> None:
    """The notice must stay free of keys, paths, and run identifiers."""
    result = _run_ratchet_report(
        GC_WITH_RATCHET="true",
        GC_RESTORE_STEP_OUTCOME="success",
        GC_RESTORE_CACHE_HIT="true",
        GC_SAVE_STEP_OUTCOME="success",
    )

    assert "ratchet-baseline-" not in result.stdout
    assert "coverage-baseline" not in result.stdout


#: Step outputs the reporter reads, mapped to the variable each must arrive in.
#: The shell fragment is tested directly, so only this wiring stands between a
#: correct script and a notice full of empty values.
REPORT_ENVIRONMENT = {
    "GC_WITH_RATCHET": "${{ inputs.with-ratchet }}",
    "GC_RESTORE_STEP_OUTCOME": "${{ steps.restore-baselines.outcome }}",
    "GC_RESTORE_CACHE_HIT": "${{ steps.restore-baselines.outputs.cache-hit }}",
    "GC_SAVE_STEP_OUTCOME": "${{ steps.save-baselines.outcome }}",
}


def test_report_runs_after_the_save_whatever_happened() -> None:
    """The reporter must observe the save, and must run on failure too.

    Without ``always()`` the reporter would be skipped by the very failures it
    exists to report, and running before the save would leave its outcome
    permanently unobserved.
    """
    steps: list[dict[str, typ.Any]] = _steps()
    names = [step.get("name") for step in steps]
    assert names.index("Save baselines") < names.index(
        "Report ratchet baseline cache decisions"
    )

    report = _step_by_name("Report ratchet baseline cache decisions")
    assert "always()" in str(report["if"])


@pytest.mark.parametrize(("variable", "expression"), sorted(REPORT_ENVIRONMENT.items()))
def test_report_reads_the_step_outputs_it_describes(
    variable: str, expression: str
) -> None:
    """Each reported value must come from the step it claims to describe."""
    report = _step_by_name("Report ratchet baseline cache decisions")
    assert report["env"].get(variable) == expression


def test_reported_steps_carry_the_ids_the_report_references() -> None:
    """The referenced step ids must exist, or every outcome reads empty."""
    assert _step_by_name("Restore baselines").get("id") == "restore-baselines"
    assert _step_by_name("Save baselines").get("id") == "save-baselines"


def test_ratchet_report_writes_the_job_summary(tmp_path: Path) -> None:
    """The job summary must carry both outcomes, not just the log notice.

    The summary is where a maintainer looks after the fact, so losing it would
    be a silent regression the notice assertions could not catch.
    """
    summary = tmp_path / "summary.md"
    summary.touch()

    result = _run_ratchet_report(
        summary_path=summary,
        GC_WITH_RATCHET="true",
        GC_RESTORE_STEP_OUTCOME="success",
        GC_RESTORE_CACHE_HIT="false",
        GC_SAVE_STEP_OUTCOME="success",
    )

    assert result.returncode == 0, result.stderr
    written = summary.read_text(encoding="utf-8")
    assert "### generate-coverage ratchet cache" in written
    assert "- baseline restore: miss" in written
    assert "- baseline save: saved" in written
    assert "ratchet-baseline-" not in written


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
