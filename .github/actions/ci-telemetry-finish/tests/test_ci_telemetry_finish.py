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
from ci_telemetry_support import FIXTURES, SCRIPTS, START_SCRIPT, finish, load

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


def _meta_file(tmp_path: Path, text: str) -> Path:
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    (directory / "meta.json").write_text(text, encoding="utf-8")
    return directory


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_finish_sample_parses_as_one_row_in_the_documented_columns(
    tmp_path: Path,
) -> None:
    """The finish copy of the sample writer matches the start action's columns."""
    summarize = load("ct_summarize_parity", SCRIPTS / "summarize.py")
    rows = summarize.parse_samples(F.take_sample(str(tmp_path), now_ns=7))
    assert [row.t_ns for row in rows] == [7]


@pytest.mark.parametrize(
    "text",
    ["{not json", "[]", json.dumps({"sampler": "proc-stat"})],
    ids=["not-json", "not-an-object", "missing-fields"],
)
def test_invalid_state_gives_a_visible_state_invalid_record(
    tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Junk metadata is reported, not read as 'the start action never ran'."""
    directory = _meta_file(tmp_path, text)
    record = F.finish(directory, {})
    assert record["sampler"] == "state-invalid"
    assert "::warning title=ci-telemetry::meta.json" in capsys.readouterr().out


def test_unreadable_state_is_not_read_as_absent(tmp_path: Path) -> None:
    """A meta.json that cannot be read (here, a directory) is a state error."""
    directory = tmp_path / "ci-telemetry"
    (directory / "meta.json").mkdir(parents=True)
    with pytest.raises(F.StateError, match="unreadable"):
        F.read_meta(directory)


def test_absent_state_is_not_an_error(tmp_path: Path) -> None:
    """No meta.json means the start action never ran: None, not an error."""
    assert F.read_meta(tmp_path) is None


def test_unknown_environment_keeps_disk_and_nulls_cpu_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the container probe failed, the host's counters are not reported."""
    directory = _state(tmp_path, pid=None)
    meta = {**META, "sampler": "unknown-environment"}
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(F, "take_sample", lambda _disk: FINAL_SAMPLE)
    record = F.finish(directory, {})
    assert record["sampler"] == "unknown-environment"
    assert record["cpu_busy_cores_avg"] is None
    assert record["peak_memory_mib"] is None
    assert record["min_free_disk_mib"] == 20000


def test_a_process_that_cannot_be_inspected_is_reported_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission error reading /proc is not 'already exited'."""
    directory = _state(tmp_path, pid="4242")

    def deny(_self: Path) -> bytes:
        raise PermissionError

    monkeypatch.setattr(F.Path, "read_bytes", deny)
    assert F.stop_sampler(directory) == "inspection-failed"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGKILL")
def test_a_sampler_that_ignores_sigterm_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGTERM first; SIGKILL only once the wait runs out."""
    directory = _state(tmp_path, pid="4242")
    signals: list[int] = []
    monkeypatch.setattr(F, "_sampler_alive", lambda _pid: True)
    monkeypatch.setattr(F, "_wait_for_exit", lambda _pid: False)
    monkeypatch.setattr(F.os, "kill", lambda _pid, sig: signals.append(sig))
    assert F.stop_sampler(directory) == "killed"
    assert signals == [F.signal.SIGTERM, F.signal.SIGKILL]


def test_a_failure_inside_finish_still_prints_one_degraded_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unexpected error gives an explicit error record, and still exits 0."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "9")

    def boom(_directory: Path, _env: object) -> dict[str, object]:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(F, "finish", boom)
    assert F.main() == 0
    out = capsys.readouterr().out
    assert out.count(MARKER) == 1
    record = json.loads(
        next(ln for ln in out.splitlines() if ln.startswith(MARKER))[len(MARKER) :]
    )
    assert record["sampler"] == "error"
    assert record["error"] == "RuntimeError"
    assert record["run_id"] == 9


def test_an_unwritable_output_does_not_stop_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each output step reports its own failure; the marker is printed once."""
    record = {"sampler": "proc-stat", "schema": 1}
    env = {
        "GITHUB_OUTPUT": str(tmp_path / "missing" / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    F.emit(record, tmp_path / "state", env)
    out = capsys.readouterr().out
    assert out.count(MARKER) == 1
    assert "outputs not written" in out
    assert (tmp_path / "summary.md").read_text().startswith("### CI telemetry")
    assert (tmp_path / "state" / "record.json").exists()


def test_outputs_and_summary_are_skipped_when_unset(tmp_path: Path) -> None:
    """Without the GitHub variables nothing is written beside the record."""
    F.write_outputs({"sampler": "x"}, tmp_path / "r.json", {})
    F.write_job_summary({"sampler": "x"}, {})
    assert list(tmp_path.iterdir()) == []


def test_outputs_name_the_record_path_and_sampler(tmp_path: Path) -> None:
    """The two declared outputs are written as name=value lines."""
    target = tmp_path / "out"
    F.write_outputs(
        {"sampler": "proc-stat"}, tmp_path / "r.json", {"GITHUB_OUTPUT": str(target)}
    )
    assert target.read_text().splitlines() == [
        f"record-path={tmp_path / 'r.json'}",
        "sampler=proc-stat",
    ]
