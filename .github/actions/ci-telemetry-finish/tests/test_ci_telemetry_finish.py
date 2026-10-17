"""The finish script's behaviour: stopping the sampler and printing the record.

The runner's orphan-process cleanup runs after a job's steps, so by the time
``ci-telemetry-finish`` runs the sampler may already be gone. These tests hold
the record to being built from the samples on disk in that case, and drive the
real start and finish scripts end to end on Linux.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from ci_telemetry_support import FIXTURES, SCRIPTS, START_SCRIPT, finish

F = finish()
MARKER = "DF12_" + "CI_TELEMETRY"
META = {
    "format": 1,
    "sampler": "proc-stat",
    "nproc": 4,
    "clk_tck": 100,
    "interval_seconds": 10,
    "disk_path": ".",
    "started_ns": 1_000_000_000_000,
}
FINAL_SAMPLE = "\t".join(
    ["1050000000000", "5004500", "1000", "2000500", "90015000", "40000", "500",
     "30000", "7000", "0", "0", "16384000", "12288000", "20480000"]
)  # fmt: skip


def _state(tmp_path: Path, *, pid: str | None) -> Path:
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    (directory / "meta.json").write_text(json.dumps(META), encoding="utf-8")
    fixture = (FIXTURES / "one-core-of-four.tsv").read_text(encoding="utf-8")
    (directory / "samples.tsv").write_text(fixture, encoding="utf-8")
    if pid is not None:
        (directory / "sampler.pid").write_text(pid + "\n", encoding="utf-8")
    return directory


def _dead_pid() -> str:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return str(child.pid)


@pytest.mark.parametrize(
    ("pid_kind", "state"),
    [("dead", "already-exited"), ("missing", "no-pid"), ("garbage", "no-pid")],
)
def test_record_is_built_from_disk_when_the_sampler_is_already_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pid_kind: str,
    state: str,
) -> None:
    """A dead sampler leaves the samples; finish still produces the record."""
    pid = {"dead": _dead_pid(), "missing": None, "garbage": "not-a-pid"}[pid_kind]
    directory = _state(tmp_path, pid=pid)
    monkeypatch.setattr(F, "take_sample", lambda _disk: FINAL_SAMPLE)
    record = F.finish(directory, {"GITHUB_JOB": "build"})
    assert record["sampler"] == "proc-stat"
    assert record["_sampler_state"] == state
    # Five samples on disk plus finish's own final one: the last interval counts.
    assert record["sample_count"] == 6
    assert record["wall_seconds"] == 50.0
    # Every interval, the final one included, held 1000 busy jiffies of 4000.
    assert record["cpu_busy_cores_avg"] == 1.0
    assert record["cpu_core_seconds"] == 50.0


def test_final_sample_is_taken_even_when_the_sampler_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last interval is never lost: the final sample joins the file."""
    directory = _state(tmp_path, pid=_dead_pid())
    monkeypatch.setattr(F, "take_sample", lambda _disk: FINAL_SAMPLE)
    F.finish(directory, {})
    lines = (directory / "samples.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[-1] == FINAL_SAMPLE


def test_no_meta_file_gives_a_not_started_record(tmp_path: Path) -> None:
    """A job that never ran the start action still prints a record."""
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    record = F.finish(directory, {})
    assert record["sampler"] == "not-started"
    assert record["sample_count"] == 0


def _run(
    script: Path, runner_temp: Path, **extra: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "RUNNER_TEMP": str(runner_temp), **extra}
    return subprocess.run(  # noqa: S603,TID251 - the scripts under test.
        [sys.executable, str(script), *(["start"] if script == START_SCRIPT else [])],
        capture_output=True,
        text=True,
        env=env,
        cwd=runner_temp,
        check=False,
        timeout=60,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_start_then_finish_prints_exactly_one_marker_line(tmp_path: Path) -> None:
    """The real scripts, end to end: one marker line, sampler stopped."""
    started = _run(START_SCRIPT, tmp_path, CT_INTERVAL_SECONDS="1")
    assert started.returncode == 0, started.stderr
    pid = int((tmp_path / "ci-telemetry" / "sampler.pid").read_text().strip())
    time.sleep(3.3)
    output = tmp_path / "github_output"
    summary = tmp_path / "summary.md"
    done = _run(
        SCRIPTS / "finish.py",
        tmp_path,
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        GITHUB_JOB="calibration",
    )
    assert done.returncode == 0, done.stderr
    marker_lines = [ln for ln in done.stdout.splitlines() if ln.startswith(MARKER)]
    assert len(marker_lines) == 1
    assert done.stdout.count(MARKER) == 1
    record = json.loads(marker_lines[0].split(" ", 1)[1])
    assert record["sampler"] in {"proc-stat", "container"}
    assert record["sample_count"] >= 4
    assert record["job"] == "calibration"
    assert f"sampler={record['sampler']}" in output.read_text()
    assert "| Measure | Value |" in summary.read_text()
    # A stopped process may linger as a zombie, which has an empty cmdline.
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        cmdline = b""
    assert b"sampler.py" not in cmdline


def test_finish_exits_zero_on_unreadable_state(tmp_path: Path) -> None:
    """Telemetry never fails a job, even when its own state is junk."""
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    (directory / "meta.json").write_text("{not json", encoding="utf-8")
    (directory / "samples.tsv").write_bytes(b"\xff\xfe\x00")
    done = _run(SCRIPTS / "finish.py", tmp_path)
    assert done.returncode == 0
    assert done.stdout.count(MARKER) <= 1
