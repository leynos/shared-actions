"""Behaviour tests for the ratchet floor.

Both ratchet scripts (``generate-coverage``, also used by
``publish-coverage-baseline``, and the older ``ratchet-coverage``) are run
against baseline files, and each action's manifest is checked to hand the
``ratchet-floor`` input to the step that runs the script.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

ACTIONS_DIR = Path(__file__).resolve().parents[2]
GENERATE_SCRIPT = ACTIONS_DIR / "generate-coverage" / "scripts" / "ratchet_coverage.py"
LEGACY_SCRIPT = ACTIONS_DIR / "ratchet-coverage" / "scripts" / "ratchet_coverage.py"

FLOOR = 85.0


def _load(script: Path) -> ModuleType:
    """Import a ratchet script under a unique module name."""
    name = f"ratchet_floor_{script.parent.parent.name.replace('-', '_')}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[GENERATE_SCRIPT, LEGACY_SCRIPT], ids=["generate", "legacy"])
def module(request: pytest.FixtureRequest) -> ModuleType:
    """Both scripts, which share the floor rule."""
    return _load(request.param)


class _Scenario(typ.TypedDict):
    """One ratchet run: the stored baseline, the new figure, and the options."""

    baseline: float
    current: float
    floor: typ.NotRequired[float]
    language: typ.NotRequired[str]


def _run(
    module: ModuleType, tmp_path: Path, **scenario: typ.Unpack[_Scenario]
) -> tuple[Path, int]:
    """Run ``main`` once and return the baseline file and the exit code."""
    file = tmp_path / ".coverage-baseline"
    file.write_text(f"{scenario['baseline']:.2f}")
    try:
        module.main(
            baseline_file=file,
            current=scenario["current"],
            floor=scenario.get("floor", FLOOR),
            language=scenario.get("language", "rust"),
        )
    except typer.Exit as exc:
        return file, exc.exit_code
    return file, 0


# --- baseline above the floor: only the floor gates, baseline tracks ----------


@pytest.mark.parametrize(
    ("baseline", "current"),
    [(95.0, 99.0), (95.0, 95.0), (95.0, 94.5), (95.0, 90.0), (95.0, FLOOR)],
)
def test_above_floor_passes_and_baseline_tracks_current(
    module: ModuleType, tmp_path: Path, baseline: float, current: float
) -> None:
    """Above the floor the run passes and the baseline becomes the current figure."""
    file, code = _run(module, tmp_path, baseline=baseline, current=current)

    assert code == 0
    assert file.read_text() == f"{current:.2f}"


def test_falling_below_floor_from_above_fails(
    module: ModuleType, tmp_path: Path
) -> None:
    """A run just under the floor fails a baseline that was above it."""
    file, code = _run(module, tmp_path, baseline=95.0, current=84.99)

    assert code == 1
    assert file.read_text() == "95.00"


def test_baseline_just_above_floor_is_in_floor_mode(
    module: ModuleType, tmp_path: Path
) -> None:
    """A baseline of 85.01 may fall to the floor itself."""
    file, code = _run(module, tmp_path, baseline=85.01, current=FLOOR)

    assert code == 0
    assert file.read_text() == "85.00"


# --- baseline at or below the floor: ratchet --------------------------------


def test_baseline_exactly_at_floor_is_ratcheted(
    module: ModuleType, tmp_path: Path
) -> None:
    """A baseline of exactly the floor is judged by the ratchet, not the floor."""
    file, code = _run(module, tmp_path, baseline=FLOOR, current=83.0)

    assert code == 1
    assert file.read_text() == "85.00"


def test_baseline_at_floor_advances_into_floor_mode(
    module: ModuleType, tmp_path: Path
) -> None:
    """Coverage rising from the floor lifts the baseline; it then tracks."""
    file, code = _run(module, tmp_path, baseline=FLOOR, current=90.0)
    assert code == 0
    assert file.read_text() == "90.00"

    file, code = _run(module, tmp_path, baseline=90.0, current=86.0)
    assert code == 0
    assert file.read_text() == "86.00"


def test_below_floor_baseline_still_fails_on_a_drop(
    module: ModuleType, tmp_path: Path
) -> None:
    """Below the floor a drop is a failure and the baseline is kept."""
    file, code = _run(module, tmp_path, baseline=60.0, current=50.0)

    assert code == 1
    assert file.read_text() == "60.00"


def test_below_floor_baseline_advances_on_improvement(
    module: ModuleType, tmp_path: Path
) -> None:
    """Below the floor an improvement is recorded, however small for legacy."""
    file, code = _run(module, tmp_path, baseline=60.0, current=70.0)

    assert code == 0
    assert file.read_text() == "70.00"


def test_current_at_floor_with_low_baseline_passes(
    module: ModuleType, tmp_path: Path
) -> None:
    """Crossing the floor from below passes and records the new figure."""
    file, code = _run(module, tmp_path, baseline=70.0, current=FLOOR)

    assert code == 0
    assert file.read_text() == "85.00"


# --- warning ----------------------------------------------------------------


def test_drop_beyond_one_point_above_floor_warns_with_language(
    module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A drop of more than one point from a baseline above the floor warns."""
    _, code = _run(module, tmp_path, baseline=95.0, current=93.99, language="python")

    out = capsys.readouterr().out
    assert code == 0
    warnings = [line for line in out.splitlines() if line.startswith("::warning")]
    assert len(warnings) == 1
    assert "python" in warnings[0].split("::")[1]


def test_drop_of_exactly_one_point_does_not_warn(
    module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A drop of exactly baseline - 1.0 is within tolerance: no warning."""
    _, code = _run(module, tmp_path, baseline=95.0, current=94.0)

    assert code == 0
    assert "::warning" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    [
        (64.01, 63.01, False),
        (64.01, 63.00, True),
        (70.07, 69.07, False),
        (70.07, 69.06, True),
        (60.01, 59.01, False),
    ],
    ids=str,
)
def test_warning_boundary_is_exact_with_a_custom_floor(
    module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[float, float, bool],
) -> None:
    """Fractional figures are compared in hundredths, not as binary floats."""
    baseline, current, warns = case
    _, code = _run(module, tmp_path, baseline=baseline, current=current, floor=50.0)

    assert code == 0
    assert ("::warning" in capsys.readouterr().out) is warns


def test_dead_band_edge_is_exact_for_fractional_baselines(tmp_path: Path) -> None:
    """A drop of exactly the tolerance passes below the floor despite float error."""
    module = _load(GENERATE_SCRIPT)
    file, code = _run(module, tmp_path, baseline=64.01, current=63.01, floor=90.0)

    assert code == 0
    assert file.read_text() == "64.01"


@pytest.mark.parametrize(
    ("floor", "current", "code"),
    [(85.004, 85.00, 1), (85.004, 85.01, 0), (85.0049, 85.0, 1), (72.555, 72.55, 1)],
)
def test_floor_keeps_its_precision(
    module: ModuleType, tmp_path: Path, floor: float, current: float, code: int
) -> None:
    """A floor with more than two decimals is not rounded in the comparison."""
    _, exit_code = _run(module, tmp_path, baseline=90.0, current=current, floor=floor)

    assert exit_code == code


def test_non_finite_current_is_refused(module: ModuleType, tmp_path: Path) -> None:
    """Both scripts refuse a NaN figure instead of passing it."""
    result = CliRunner().invoke(
        _cli(module),
        ["--baseline-file", str(tmp_path / "b"), "--current", "nan"],
    )

    assert result.exit_code == 2


def test_non_finite_baseline_reads_as_zero(module: ModuleType, tmp_path: Path) -> None:
    """A baseline file holding ``nan`` is treated as missing, like any junk."""
    file, code = _run(module, tmp_path, baseline=float("nan"), current=70.0)

    assert code == 0
    assert file.read_text() == "70.00"


def test_no_warning_for_rises_or_ratcheted_baselines(
    module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only a drop from a baseline above the floor warns."""
    _run(module, tmp_path, baseline=95.0, current=99.0)
    _run(module, tmp_path, baseline=60.0, current=70.0)
    _run(module, tmp_path, baseline=60.0, current=50.0)

    assert "::warning" not in capsys.readouterr().out


def test_failing_run_does_not_warn(
    module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run below the floor fails; the failure is the signal, not a warning."""
    _, code = _run(module, tmp_path, baseline=95.0, current=50.0)

    assert code == 1
    assert "::warning" not in capsys.readouterr().out


# --- floor 0 parity -----------------------------------------------------------


def test_floor_zero_ratchets_a_high_baseline(
    module: ModuleType, tmp_path: Path
) -> None:
    """With the floor off a drop from a high baseline fails, as it used to."""
    file, code = _run(module, tmp_path, baseline=95.0, current=84.0, floor=0.0)

    assert code == 1
    assert file.read_text() == "95.00"


def test_floor_zero_never_warns_and_never_tracks_down(
    module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the floor off, a pass never lowers the baseline or warns."""
    file, code = _run(module, tmp_path, baseline=95.0, current=95.0, floor=0.0)

    assert code == 0
    assert file.read_text() == "95.00"
    assert "::warning" not in capsys.readouterr().out


# --- the shared script's dead-band, with the floor in play ---------------------


def test_generate_script_holds_baseline_inside_band_below_floor(
    tmp_path: Path,
) -> None:
    """The dead-band survives below the floor: a dip within 1.0 passes, held."""
    module = _load(GENERATE_SCRIPT)
    file, code = _run(module, tmp_path, baseline=80.0, current=79.5)

    assert code == 0
    assert file.read_text() == "80.00"


def test_generate_script_fails_at_just_beyond_band_below_floor(
    tmp_path: Path,
) -> None:
    """A drop of more than 1.0 below a baseline at the floor fails."""
    module = _load(GENERATE_SCRIPT)
    file, code = _run(module, tmp_path, baseline=FLOOR, current=83.99)

    assert code == 1
    assert file.read_text() == "85.00"


def test_generate_script_passes_at_exactly_one_point_below_baseline(
    tmp_path: Path,
) -> None:
    """Current exactly baseline - 1.0 passes in ratchet mode."""
    module = _load(GENERATE_SCRIPT)
    _, code = _run(module, tmp_path, baseline=FLOOR, current=84.0)

    assert code == 0


def test_legacy_script_has_no_tolerance_below_the_floor(tmp_path: Path) -> None:
    """The older action stays strict: any drop below a ratcheted baseline fails."""
    module = _load(LEGACY_SCRIPT)
    file, code = _run(module, tmp_path, baseline=FLOOR, current=84.9)

    assert code == 1
    assert file.read_text() == "85.00"


# --- validation -------------------------------------------------------------------


def _cli(module: ModuleType) -> typer.Typer:
    """Wrap ``module.main`` in a Typer app so the CLI parsing is exercised."""
    app = typer.Typer()
    app.command()(module.main)
    return app


@pytest.mark.parametrize("bad", ["-1", "100.5", "nan", "inf", "-inf", "abc", ""])
def test_invalid_floor_is_refused(module: ModuleType, tmp_path: Path, bad: str) -> None:
    """A floor that is not a number from 0 to 100 is a usage error."""
    file = tmp_path / ".coverage-baseline"
    file.write_text("90.00")

    result = CliRunner().invoke(
        _cli(module),
        ["--baseline-file", str(file), "--current", "90", "--floor", bad],
    )

    assert result.exit_code == 2
    assert file.read_text() == "90.00"


@pytest.mark.parametrize("good", ["0", "85", "100", "72.5"])
def test_valid_floor_is_accepted(module: ModuleType, tmp_path: Path, good: str) -> None:
    """The ends of the range and values between are accepted."""
    file = tmp_path / ".coverage-baseline"
    file.write_text("100")

    result = CliRunner().invoke(
        _cli(module),
        ["--baseline-file", str(file), "--current", "100", "--floor", good],
    )

    assert result.exit_code == 0


def test_floor_defaults_to_85(module: ModuleType, tmp_path: Path) -> None:
    """Without a floor argument the script applies 85."""
    file = tmp_path / ".coverage-baseline"
    file.write_text("95")

    low = CliRunner().invoke(
        _cli(module), ["--baseline-file", str(file), "--current", "84"]
    )
    ok = CliRunner().invoke(
        _cli(module), ["--baseline-file", str(file), "--current", "86"]
    )

    assert low.exit_code == 1
    assert ok.exit_code == 0


def test_floor_is_read_from_the_environment(module: ModuleType, tmp_path: Path) -> None:
    """``INPUT_RATCHET_FLOOR`` supplies the floor when the flag is absent."""
    file = tmp_path / ".coverage-baseline"
    file.write_text("95")

    result = CliRunner().invoke(
        _cli(module),
        ["--baseline-file", str(file), "--current", "50"],
        env={"INPUT_RATCHET_FLOOR": "40"},
    )

    assert result.exit_code == 0


# --- manifest wiring ------------------------------------------------------------


@pytest.mark.parametrize(
    "action", ["generate-coverage", "publish-coverage-baseline", "ratchet-coverage"]
)
def test_action_hands_the_floor_to_its_ratchet_step(action: str) -> None:
    """Each action exposes ``ratchet-floor`` (default 85) and feeds it to the step."""
    data = yaml.safe_load((ACTIONS_DIR / action / "action.yml").read_text())

    assert str(data["inputs"]["ratchet-floor"]["default"]) == "85"
    steps = [s for s in data["runs"]["steps"] if s.get("name") == "Ratchet coverage"]
    assert len(steps) == 1
    values = [str(v) for v in steps[0].get("env", {}).values()]
    assert "${{ inputs.ratchet-floor }}" in values
