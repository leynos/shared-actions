"""The calibration helpers the dispatch-only workflow runs.

``ci_telemetry_load.py`` holds k processors busy; ``ci_telemetry_verdict.py``
judges a record against gate G3 of the CI observability plan: `nproc` equals
the label's vCPU count, busy cores read k within 0.2 cores and 10%, and steal
stays under 5%. Wrong boundaries here would pass or fail a runner shape on the
evidence the plan rests on.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml
from ci_telemetry_support import ACTION_DIR, load

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
VERDICT = load("ct_verdict", SCRIPTS / "ci_telemetry_verdict.py")
LOAD = load("ct_load", SCRIPTS / "ci_telemetry_load.py")
START_ACTION = ACTION_DIR.parent / "ci-telemetry-start" / "action.yml"
FINISH_ACTION = ACTION_DIR / "action.yml"


def _record(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "nproc": 4,
        "cpu_busy_cores_avg": 1.0,
        "cpu_busy_cores_p95": 1.0,
        "cpu_busy_cores_max": 1.0,
        "cpu_steal_fraction": 0.0,
        "sample_count": 13,
    }
    return {**base, **changes}


@pytest.mark.parametrize(
    ("avg", "within_cores", "within_ratio"),
    [
        (1.0, True, True),
        (1.1, True, True),  # exactly 10% of k = 1: inclusive
        (1.1001, True, False),
        (1.2, True, False),  # exactly 0.2 cores: inclusive
        (1.2001, False, False),
        (0.9, True, True),
        (0.7, False, False),
    ],
)
def test_load_one_is_judged_against_one_core(
    avg: float, *, within_cores: bool, within_ratio: bool
) -> None:
    """With k = 1 both tolerances are read from one core, edges inclusive."""
    verdict = VERDICT.judge(_record(cpu_busy_cores_avg=avg), 4, "one")
    assert verdict["k"] == 1
    assert verdict["busy_within_0_2"] is within_cores
    assert verdict["busy_within_10pct"] is within_ratio


@pytest.mark.parametrize(
    ("avg", "within_cores", "within_ratio"),
    [(4.0, True, True), (3.8, True, True), (3.6, False, True), (3.5, False, False)],
)
def test_load_all_is_judged_against_nproc(
    avg: float, *, within_cores: bool, within_ratio: bool
) -> None:
    """With k = nproc the 10% tolerance scales (0.4 cores at 4)."""
    verdict = VERDICT.judge(_record(cpu_busy_cores_avg=avg), 4, "all")
    assert verdict["k"] == 4
    assert verdict["busy_within_0_2"] is within_cores
    assert verdict["busy_within_10pct"] is within_ratio


@pytest.mark.parametrize(
    ("steal", "expected"),
    [(0.0, True), (0.0499, True), (0.05, False), (0.2, False), (None, False)],
)
def test_steal_must_stay_under_five_percent(
    steal: float | None, *, expected: bool
) -> None:
    """The steal limit is strict, and a missing figure is not a pass."""
    verdict = VERDICT.judge(_record(cpu_steal_fraction=steal), 4, "one")
    assert verdict["steal_under_5pct"] is expected


@pytest.mark.parametrize(("nproc", "expected"), [(4, True), (2, False), (8, False)])
def test_nproc_must_equal_the_label(nproc: int, *, expected: bool) -> None:
    """A machine presenting a different count than its label is falsification."""
    verdict = VERDICT.judge(_record(nproc=nproc), 4, "one")
    assert verdict["nproc_matches_label"] is expected


@pytest.mark.parametrize("avg", [None, "n/a"])
def test_an_unmeasured_record_passes_no_busy_criterion(avg: object) -> None:
    """Null or junk CPU readings fail the busy criteria rather than crash."""
    verdict = VERDICT.judge(_record(cpu_busy_cores_avg=avg), 4, "one")
    assert verdict["busy_within_0_2"] is False
    assert verdict["busy_within_10pct"] is False


def test_the_cli_prints_one_notice_with_the_verdict_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The notice annotation carries the label, load and every judgement."""
    path = tmp_path / "record.json"
    path.write_text(json.dumps(_record()), encoding="utf-8")
    argv = ["verdict.py", str(path), "ubicloud-standard-4", "4", "one"]
    assert VERDICT.main(argv) == 0
    (line,) = capsys.readouterr().out.splitlines()
    prefix = "::notice title=ci-telemetry calibration::"
    assert line.startswith(prefix)
    verdict = json.loads(line[len(prefix) :])
    assert verdict["label"] == "ubicloud-standard-4"
    assert verdict["load"] == "one"
    assert verdict["nproc_matches_label"] is True
    assert verdict["busy_avg"] == 1.0


def test_the_cli_rejects_the_wrong_argument_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Usage errors exit 2 and say how to call it."""
    assert VERDICT.main(["verdict.py", "only-one"]) == 2
    assert "usage: verdict.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv", [["load.py"], ["load.py", "some", "1"], ["load.py", "one"]]
)
def test_load_rejects_bad_arguments(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A usage error exits 2 before starting any process."""
    assert LOAD.main(argv) == 2
    assert "usage: load.py" in capsys.readouterr().err


class _Worker:
    """A stand-in for ``multiprocessing.Process`` that records its lifecycle."""

    instances: typ.ClassVar[list[_Worker]] = []

    def __init__(self, target: object, args: tuple[float]) -> None:
        self.target = target
        self.args = args
        self.events: list[str] = []
        _Worker.instances.append(self)

    def start(self) -> None:
        self.events.append("start")

    def join(self) -> None:
        self.events.append("join")


@pytest.mark.skipif(not hasattr(os, "sched_getaffinity"), reason="Linux only")
@pytest.mark.parametrize(("mode", "expected"), [("one", 1), ("all", None)])
def test_load_starts_and_joins_one_worker_per_core(
    mode: str, expected: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``one`` runs one process; ``all`` runs one per processor available."""
    _Worker.instances = []
    monkeypatch.setattr(LOAD.multiprocessing, "Process", _Worker)
    assert LOAD.main(["load.py", mode, "60"]) == 0
    count = len(os.sched_getaffinity(0)) if expected is None else expected
    assert len(_Worker.instances) == count
    assert all(w.events == ["start", "join"] for w in _Worker.instances)
    assert all(w.args == (60.0,) for w in _Worker.instances)
    assert all(w.target is LOAD.spin for w in _Worker.instances)


def test_spin_holds_for_the_requested_time() -> None:
    """The burn lasts at least as long as asked."""
    started = LOAD.time.monotonic()
    LOAD.spin(0.2)
    assert LOAD.time.monotonic() - started >= 0.2


def test_load_really_runs_and_exits_zero() -> None:
    """The script end to end with a short duration."""
    done = subprocess.run(  # noqa: S603,TID251 - the script under test.
        [
            shutil.which("python3") or "python3",
            str(SCRIPTS / "ci_telemetry_load.py"),
            "one",
            "0.3",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0
    assert "1 busy process(es)" in done.stdout


# --- the action manifests' shell fragments -------------------------------


def _run_fragment(
    action: Path, tmp_path: Path, env: dict[str, str], *, python: str | None
) -> subprocess.CompletedProcess[str]:
    """Run the action's single step with a PATH holding only *python*, if any."""
    step = yaml.safe_load(action.read_text(encoding="utf-8"))["runs"]["steps"][0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if python is not None:
        stub = bin_dir / python
        stub.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\n', encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [shutil.which("bash") or "bash", "-c", step["run"]],
        capture_output=True,
        text=True,
        env={"PATH": str(bin_dir), **env},
        check=False,
        timeout=30,
    )


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize(
    ("action", "variable"),
    [(START_ACTION, "CT_SAMPLER"), (FINISH_ACTION, "CT_FINISH")],
    ids=["start", "finish"],
)
class TestManifestFragments:
    """Both actions warn and exit 0 with no Python, and otherwise run the script."""

    def test_no_python_is_a_warning_not_a_failure(
        self, action: Path, variable: str, tmp_path: Path
    ) -> None:
        """Telemetry is off for the job; the job is not failed."""
        done = _run_fragment(action, tmp_path, {variable: "x.py"}, python=None)
        assert done.returncode == 0
        assert "::warning title=ci-telemetry::no Python on PATH" in done.stdout

    @pytest.mark.parametrize("interpreter", ["python3", "python"])
    def test_either_interpreter_runs_the_script(
        self, action: Path, variable: str, interpreter: str, tmp_path: Path
    ) -> None:
        """python3 is preferred, python is the fallback; the script is the argument."""
        done = _run_fragment(
            action, tmp_path, {variable: "the-script.py"}, python=interpreter
        )
        assert done.returncode == 0, done.stderr
        args = (tmp_path / "args").read_text(encoding="utf-8").split()
        assert args[0] == "the-script.py"
        assert args[1:] == (["start"] if action == START_ACTION else [])


def test_start_declares_its_inputs_with_defaults() -> None:
    """Both inputs are optional strings with the documented defaults."""
    inputs = yaml.safe_load(START_ACTION.read_text(encoding="utf-8"))["inputs"]
    assert {k: (v["required"], v["default"]) for k, v in inputs.items()} == {
        "interval-seconds": (False, "10"),
        "disk-path": (False, "."),
    }


def test_finish_outputs_come_from_its_step() -> None:
    """Each declared output reads the finish step's output of the same name."""
    outputs = yaml.safe_load(FINISH_ACTION.read_text(encoding="utf-8"))["outputs"]
    assert {k: v["value"] for k, v in outputs.items()} == {
        "record-path": "${{ steps.finish.outputs.record-path }}",
        "sampler": "${{ steps.finish.outputs.sampler }}",
    }


WORKFLOW = (
    Path(__file__).resolve().parents[3] / "workflows" / "calibrate-ci-telemetry.yml"
)


def _calibration_steps() -> list[dict[str, typ.Any]]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["calibrate"]["steps"]


def test_the_workflow_runs_existing_helpers_with_the_matrix_load() -> None:
    """The burn and verdict steps name scripts that exist, in the right order."""
    steps = _calibration_steps()
    commands = [str(step.get("run", "")) for step in steps]
    burn = next(c for c in commands if "ci_telemetry_load.py" in c)
    verdict = next(c for c in commands if "ci_telemetry_verdict.py" in c)
    assert burn.split()[1] == ".github/scripts/ci_telemetry_load.py"
    assert burn.split()[2:] == ['"${LOAD}"', "60"]
    assert verdict.split()[1] == ".github/scripts/ci_telemetry_verdict.py"
    assert verdict.split()[2:] == [
        '"${RECORD_PATH}"',
        '"${RUNNER_LABEL}"',
        '"${LABEL_CORES}"',
        '"${LOAD}"',
    ]
    assert commands.index(burn) < commands.index(verdict)
    repository = WORKFLOW.parents[2]
    assert (repository / ".github/scripts/ci_telemetry_load.py").is_file()
    assert (repository / ".github/scripts/ci_telemetry_verdict.py").is_file()


def test_the_workflow_wires_the_matrix_into_the_helpers() -> None:
    """LOAD and the label come from the matrix; the record from the finish step."""
    steps = {step.get("name"): step for step in _calibration_steps()}
    assert steps["Hold the load"]["env"] == {"LOAD": "${{ matrix.load }}"}
    judge = steps["Judge against G3"]
    assert judge["env"]["RECORD_PATH"] == "${{ steps.telemetry.outputs.record-path }}"
    assert judge["env"]["RUNNER_LABEL"] == "${{ matrix.runner }}"
    assert judge["env"]["LABEL_CORES"] == "${{ matrix.label-cores }}"


def test_finish_and_the_verdict_run_even_when_the_burn_fails() -> None:
    """A failed leg still reports what it measured."""
    steps = _calibration_steps()
    finish = next(s for s in steps if "ci-telemetry-finish" in str(s.get("uses", "")))
    assert finish["if"] == "always()"
    assert finish["id"] == "telemetry"
    judge = next(s for s in steps if s.get("name") == "Judge against G3")
    assert judge["if"].startswith("always()")
