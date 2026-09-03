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
* The split between the ``actions/cache/restore`` and ``actions/cache/save``
  sub-actions. The full ``actions/cache`` action registers its own post-job
  save, so using it for the restore step made two writers race for the same
  run-id key and every run logged "Unable to reserve cache ... already exists".
* That every ``actions/cache`` reference is pinned to a commit SHA rather than
  a moving tag.
* The reader/writer invariant the split exists to satisfy, as a property over
  every pairing of the three cache action variants, plus the bounded outcomes
  the reporting step emits for each half.
* The ``ratchet_coverage.py`` script's baseline-advance semantics: the stored
  baseline rises when coverage improves, holds when coverage is unchanged, and
  the gate fails when coverage drops below the baseline.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
import typer
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

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


#: Composite manifests whose ``actions/cache`` references this test guards.
#: Both are edited together whenever the pinned cache revision moves.
CACHE_PINNED_MANIFESTS = (
    ACTION_YML,
    ACTION_DIR.parent / "rust-build-release" / "action.yml",
)

#: Sub-action references the two ratchet cache steps must use, mapped to the
#: step that owns each half of the restore/save pair.
RATCHET_CACHE_SUBACTIONS = {
    "Restore baselines": "actions/cache/restore",
    "Save baselines": "actions/cache/save",
}

_ACTION_SHA_PATTERN = re.compile(r"^(?P<action>[^@]+)@(?P<sha>[0-9a-f]{40})$")


def _ratchet_cache_reference(step_name: str) -> re.Match[str]:
    """Return the parsed ``uses`` reference for a ratchet cache step."""
    uses = _step_by_name(step_name)["uses"]
    match = _ACTION_SHA_PATTERN.fullmatch(uses)
    assert match is not None, f"{step_name!r} must pin a full commit SHA, got: {uses}"
    return match


@pytest.mark.parametrize(
    ("step_name", "expected_action"), sorted(RATCHET_CACHE_SUBACTIONS.items())
)
def test_ratchet_cache_steps_use_the_split_subactions(
    step_name: str, expected_action: str
) -> None:
    """Let exactly one step write the ratchet key.

    The full ``actions/cache`` action registers a post-job save of its own, so
    pairing it with an explicit save step made both contend for the same
    run-id key and fail the reservation on every run.
    """
    assert _ratchet_cache_reference(step_name)["action"] == expected_action


def test_ratchet_cache_steps_share_one_pinned_revision() -> None:
    """Both halves of the pair must come from the same pinned release."""
    shas = {
        _ratchet_cache_reference(step_name)["sha"]
        for step_name in RATCHET_CACHE_SUBACTIONS
    }
    assert len(shas) == 1, f"ratchet cache steps pin differing revisions: {shas}"


def _cache_references(manifest: Path) -> list[str]:
    """Return every ``actions/cache`` reference declared in *manifest*."""
    steps = yaml.safe_load(manifest.read_text())["runs"]["steps"]
    return [
        uses
        for step in steps
        if isinstance(uses := step.get("uses"), str)
        and uses.split("@", 1)[0].split("/")[:2] == ["actions", "cache"]
    ]


@pytest.mark.parametrize(
    "manifest",
    sorted(CACHE_PINNED_MANIFESTS),
    ids=lambda manifest: manifest.parent.name,
)
def test_cache_references_are_sha_pinned(manifest: Path) -> None:
    """No manifest may reach `actions/cache` through a floating tag.

    A moving tag such as ``@v4`` breaks the repository's pinning policy, and
    the older releases it can resolve to are not intercepted by a transparent
    runner cache, so their saves are wasted upload.
    """
    unpinned = [
        uses
        for uses in _cache_references(manifest)
        if not _ACTION_SHA_PATTERN.fullmatch(uses)
    ]
    assert not unpinned, f"{manifest.name} has unpinned cache references: {unpinned}"


#: How each cache action variant participates in the baseline lifecycle.
#: The full action both restores and registers a post-job save, which is why
#: pairing it with an explicit save step gave the run-id key two writers.
CACHE_VARIANT_ROLES = {
    "actions/cache": (1, 1),
    "actions/cache/restore": (1, 0),
    "actions/cache/save": (0, 1),
}


def _lifecycle_is_sound(first_variant: str, second_variant: str) -> bool:
    """Return whether a pairing reads once, then writes once, in that order.

    The baseline cache has one legal shape: the earlier step restores and must
    not write, and the later step writes and must not restore. Anything else
    either gives the run-id key two writers, which loses the reservation, or
    restores a second time after the ratchet has already advanced the file.
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


def test_manifest_satisfies_the_lifecycle_invariant() -> None:
    """The shipped manifest must be the pairing the property singles out."""
    assert _lifecycle_is_sound(
        _ratchet_cache_reference("Restore baselines")["action"],
        _ratchet_cache_reference("Save baselines")["action"],
    )


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


def test_restore_precedes_save_over_matching_paths() -> None:
    """The pair must read before it writes, over exactly the same files.

    Order and paths are the rest of the lifecycle contract: a save that ran
    first would write a stale baseline, and differing path lists would save
    something other than what was restored.
    """
    steps: list[dict[str, typ.Any]] = _steps()
    names = [step.get("name") for step in steps]
    assert names.index("Restore baselines") < names.index("Save baselines")

    restore = _step_by_name("Restore baselines")
    save = _step_by_name("Save baselines")
    assert restore["with"]["path"] == save["with"]["path"]


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
