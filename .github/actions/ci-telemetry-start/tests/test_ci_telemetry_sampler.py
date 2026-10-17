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
    / "summarise.py"
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
    summarise = _load("ct_start_peer_summarise", FINISH_SUMMARISE)
    assert tuple(SAMPLER.SAMPLE_COLUMNS) == tuple(summarise.SAMPLE_COLUMNS)
    (sample,) = summarise.parse_samples(line)
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
