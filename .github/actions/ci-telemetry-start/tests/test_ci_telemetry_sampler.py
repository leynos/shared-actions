"""The start action's sampler: machine shape, classification and raw samples."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sampler.py"
FINISH_SUMMARISE = (
    Path(__file__).resolve().parents[2]
    / "ci-telemetry-finish"
    / "scripts"
    / "summarize.py"
)


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SAMPLER = _load("ct_start_sampler", SCRIPT)

PROC_STAT = (
    "cpu  100 20 300 4000 50 6 7 8 9 10\ncpu0 50 10 150 2000 25 3 3 4 4 5\nintr 1\n"
)
MEMINFO = "MemTotal:       16384000 kB\nMemFree: 1 kB\nMemAvailable:   12288000 kB\n"


def test_parse_cpu_line_reads_the_aggregate_line_only() -> None:
    """The ten counters of the ``cpu`` line, not a per-core line."""
    assert SAMPLER.parse_cpu_line(PROC_STAT) == [100, 20, 300, 4000, 50, 6, 7, 8, 9, 10]


def test_parse_cpu_line_pads_old_kernels_with_zero() -> None:
    """A kernel with fewer fields reads the missing ones as zero."""
    assert SAMPLER.parse_cpu_line("cpu 1 2 3 4\n") == [1, 2, 3, 4, 0, 0, 0, 0, 0, 0]


def test_parse_cpu_line_without_an_aggregate_line_fails_loudly() -> None:
    """No cpu line is an error, not a zero reading."""
    with pytest.raises(ValueError, match="aggregate cpu line"):
        SAMPLER.parse_cpu_line("cpu0 1 2 3\n")


def test_parse_meminfo_returns_total_and_available() -> None:
    """Available, not free: page cache is reclaimable."""
    assert SAMPLER.parse_meminfo(MEMINFO) == (16384000, 12288000)


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Windows", "unsupported-os"),
        ("Darwin", "unsupported-os"),
        ("Linux", "proc-stat"),
    ],
)
def test_classify_by_operating_system(
    tmp_path: Path, system: str, expected: str
) -> None:
    """Only Linux outside a container has a /proc to read."""
    assert SAMPLER.classify(system, tmp_path) == expected


def test_classify_sees_dockerenv(tmp_path: Path) -> None:
    """``/.dockerenv`` marks a container job."""
    (tmp_path / ".dockerenv").write_text("", encoding="utf-8")
    assert SAMPLER.classify("Linux", tmp_path) == "container"


@pytest.mark.parametrize("runtime", ["docker", "containerd", "kubepods"])
def test_classify_sees_container_cgroups(tmp_path: Path, runtime: str) -> None:
    """The init cgroup naming a runtime marks a container job."""
    (tmp_path / "proc/1").mkdir(parents=True)
    (tmp_path / "proc/1/cgroup").write_text(f"0::/{runtime}/abc\n", encoding="utf-8")
    assert SAMPLER.classify("Linux", tmp_path) == "container"


def test_classify_plain_vm_cgroup_is_not_a_container(tmp_path: Path) -> None:
    """A VM's init cgroup is the root or a system slice."""
    (tmp_path / "proc/1").mkdir(parents=True)
    (tmp_path / "proc/1/cgroup").write_text("0::/init.scope\n", encoding="utf-8")
    assert SAMPLER.classify("Linux", tmp_path) == "proc-stat"


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_take_sample_has_the_documented_columns(tmp_path: Path) -> None:
    """A live sample parses with the finish action's reader, one column each."""
    line = SAMPLER.take_sample(str(tmp_path), now_ns=42)
    summarize = _load("ct_start_peer_summarize", FINISH_SUMMARISE)
    assert tuple(SAMPLER.SAMPLE_COLUMNS) == tuple(summarize.SAMPLE_COLUMNS)
    (sample,) = summarize.parse_samples(line)
    assert sample.t_ns == 42
    assert sample.mem_total_kib > sample.mem_available_kib > 0
    assert sample.free_disk_kib > 0


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_start_records_the_shape_the_machine_presents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nproc is what the process may use, never derived from a label."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(SAMPLER.platform, "system", lambda: "Linux")
    monkeypatch.setattr(SAMPLER, "classify", lambda _system, _root=None: "proc-stat")
    spawned: list[list[str]] = []

    class _Child:
        pid = 4242

    def _popen(command: list[str], **_kwargs: object) -> _Child:
        spawned.append(command)
        return _Child()

    monkeypatch.setattr(SAMPLER.subprocess, "Popen", _popen)
    assert SAMPLER.start(7, str(tmp_path)) == 0
    meta = json.loads((tmp_path / "ci-telemetry" / "meta.json").read_text())
    assert meta["nproc"] == len(os.sched_getaffinity(0))
    assert meta["clk_tck"] == os.sysconf("SC_CLK_TCK")
    assert meta["interval_seconds"] == 7
    assert meta["sampler"] == "proc-stat"
    assert (tmp_path / "ci-telemetry" / "sampler.pid").read_text() == "4242\n"
    assert spawned[0][-1] == "run"
    first = (tmp_path / "ci-telemetry" / "samples.tsv").read_text().splitlines()
    assert len(first) == 1


def test_start_off_linux_takes_no_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows and macOS record the shape only: no samples, no sampler."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(SAMPLER.platform, "system", lambda: "Windows")
    assert SAMPLER.start(10, ".") == 0
    directory = tmp_path / "ci-telemetry"
    assert json.loads((directory / "meta.json").read_text())["sampler"] == (
        "unsupported-os"
    )
    assert not (directory / "samples.tsv").exists()
    assert not (directory / "sampler.pid").exists()


def test_start_clears_a_previous_jobs_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reused runner directory never leaks the last job's samples."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(SAMPLER.platform, "system", lambda: "Windows")
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    (directory / "samples.tsv").write_text("stale\n", encoding="utf-8")
    SAMPLER.start(10, ".")
    assert not (directory / "samples.tsv").exists()


def test_unreadable_container_probe_is_unknown_not_a_plain_vm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cgroup file that cannot be read is not evidence of a VM."""
    (tmp_path / "proc/1/cgroup").mkdir(parents=True)  # reading a directory fails
    assert SAMPLER.classify("Linux", tmp_path) == "unknown-environment"
    assert "container probe failed" in capsys.readouterr().out


def test_a_failed_first_sample_still_starts_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first sample failing (a disk-path checkout has not made) is a warning."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(SAMPLER.platform, "system", lambda: "Linux")
    monkeypatch.setattr(SAMPLER, "classify", lambda _system, _root=None: "proc-stat")
    spawned: list[int] = []

    class _Child:
        pid = 77

    monkeypatch.setattr(
        SAMPLER.subprocess, "Popen", lambda *_a, **_k: spawned.append(1) or _Child()
    )
    assert SAMPLER.start(5, str(tmp_path / "does-not-exist-yet")) == 0
    assert spawned == [1]
    assert "first sample failed" in capsys.readouterr().out
    directory = tmp_path / "ci-telemetry"
    assert (directory / "sampler.pid").read_text() == "77\n"
    assert not (directory / "samples.tsv").exists()


def test_a_sampler_that_cannot_be_spawned_leaves_no_pid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failure to spawn is a warning and a clean exit, with no stale PID."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(SAMPLER.platform, "system", lambda: "Linux")
    monkeypatch.setattr(SAMPLER, "classify", lambda _system, _root=None: "proc-stat")

    def refuse(*_args: object, **_kwargs: object) -> None:
        message = "no processes"
        raise OSError(message)

    monkeypatch.setattr(SAMPLER.subprocess, "Popen", refuse)
    assert SAMPLER.start(5, str(tmp_path)) == 0
    assert "sampler did not start" in capsys.readouterr().out
    assert not (tmp_path / "ci-telemetry" / "sampler.pid").exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 10), ("30", 30), ("0", 1), ("-5", 1), ("soon", 10), ("", 10)],
)
def test_interval_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    """Missing and junk values give the default; values below 1 are raised."""
    monkeypatch.delenv("CT_INTERVAL_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("CT_INTERVAL_SECONDS", value)
    assert SAMPLER._interval() == expected


def test_run_loop_skips_a_failed_sample_and_keeps_sampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each tick appends a sample; a failing tick is skipped, not fatal."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    (tmp_path / "ci-telemetry").mkdir()
    results = iter(["a", OSError("transient"), "b"])
    ticks: list[float] = []

    def fake_sample(_disk: str) -> str:
        item = next(results)
        if isinstance(item, Exception):
            raise item
        return item

    def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) > 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(SAMPLER, "take_sample", fake_sample)
    monkeypatch.setattr(SAMPLER.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        SAMPLER.run_loop(4, ".")
    assert ticks == [4, 4, 4, 4]
    lines = (tmp_path / "ci-telemetry" / "samples.tsv").read_text().splitlines()
    assert lines == ["a", "b"]


def test_main_dispatches_and_rejects_unknown_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The start and run commands get the environment's interval and path."""
    calls: list[tuple[str, int, str]] = []
    monkeypatch.setenv("CT_INTERVAL_SECONDS", "6")
    monkeypatch.setenv("CT_DISK_PATH", "/data")
    monkeypatch.setattr(
        SAMPLER, "start", lambda i, d: calls.append(("start", i, d)) or 0
    )
    monkeypatch.setattr(SAMPLER, "run_loop", lambda i, d: calls.append(("run", i, d)))
    assert SAMPLER.main(["sampler.py", "start"]) == 0
    assert SAMPLER.main(["sampler.py", "run"]) == 0
    assert calls == [("start", 6, "/data"), ("run", 6, "/data")]
    assert SAMPLER.main(["sampler.py", "stop"]) == 2
    assert "usage: sampler.py" in capsys.readouterr().err
