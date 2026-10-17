"""Exact figures from recorded ``/proc/stat`` samples.

Every fixture is a machine of 4 processors at 100 jiffies a second, sampled
every 10 s, so an interval holds 4 * 10 * 100 = 4000 jiffies of CPU time. The
expected figures below are worked by hand from the deltas, not produced by the
code under test; the comment on each says how.
"""

from __future__ import annotations

import json
import typing as typ

import pytest
from ci_telemetry_support import FIXTURES, summarise
from hypothesis import given, settings
from hypothesis import strategies as st

S = summarise()
META = {
    "format": 1,
    "sampler": "proc-stat",
    "nproc": 4,
    "clk_tck": 100,
    "interval_seconds": 10,
    "disk_path": ".",
    "started_ns": 1_000_000_000_000,
}
ENV = {
    "GITHUB_REPOSITORY": "leynos/example",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_JOB": "build",
    "GITHUB_RUN_ID": "123456789",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SHA": "a" * 40,
    "RUNNER_NAME": "ubicloud-standard-4-1",
    "RUNNER_OS": "Linux",
    "RUNNER_ARCH": "X64",
    "SECRET_TOKEN": "hunter2",
}


def _cpu(
    cores: float,
    peak: float,
    core_seconds: float,
    efficiency: float,
    p95: float | None = None,
    saturation: float = 0.0,
    steal: float = 0.0,
) -> dict[str, float]:
    return {
        "cpu_core_seconds": core_seconds,
        "cpu_busy_cores_avg": cores,
        "cpu_busy_cores_p95": cores if p95 is None else p95,
        "cpu_busy_cores_max": peak,
        "cpu_saturation_fraction": saturation,
        "cpu_steal_fraction": steal,
        "cpu_efficiency": efficiency,
    }


def _memdisk(peak_mib: int = 4000, disk_mib: int = 20000) -> dict[str, int]:
    return {
        "peak_memory_mib": peak_mib,
        "mem_total_mib": 16000,
        "min_free_disk_mib": disk_mib,
    }


#: fixture name -> (sample_count, wall_seconds, expected figures)
CASES: dict[str, tuple[int, float, dict[str, typ.Any]]] = {
    # 40 jiffies busy of 4000 an interval: 1% of 4 cores = 0.04 cores;
    # 4 * 40 / 100 = 1.6 core-seconds over 40 s * 4 cores = 0.01.
    "idle": (5, 40.0, {**_cpu(0.04, 0.04, 1.6, 0.01), **_memdisk()}),
    # 1000 busy of 4000: a quarter of 4 cores = 1.0; 40 core-seconds; 40 / 160.
    # Peak memory is 16384000 - 8192000 KiB = 8000 MiB; disk starts at the
    # minimum 20480000 KiB = 20000 MiB.
    "one-core-of-four": (5, 40.0, {**_cpu(1.0, 1.0, 40.0, 0.25), **_memdisk(8000)}),
    # 3960 busy of 4000 = 99% = 3.96 cores, above the 90% threshold in every
    # interval; 4 * 3960 / 100 = 158.4 core-seconds.
    "saturated": (
        5,
        40.0,
        {**_cpu(3.96, 3.96, 158.4, 0.99, saturation=1.0), **_memdisk()},
    ),
    # Nineteen intervals of 1000 busy and one of 4000: average
    # 23000 / 80000 * 4 = 1.15; nearest-rank p95 is the 19th of 20 = 1.0, which
    # is not the maximum 4.0; one interval in twenty saturates = 0.05;
    # 230 core-seconds over 200 s * 4 = 0.2875.
    "one-spike": (
        21,
        200.0,
        {
            **_cpu(1.15, 4.0, 230.0, 0.2875, p95=1.0, saturation=0.05),
            **_memdisk(),
        },
    ),
    # 1000 user, 1000 steal, 2000 idle: steal is not busy, so 1.0 core, not
    # 2.0; steal is 1000 of 4000 = 0.25.
    "steal-heavy": (5, 40.0, {**_cpu(1.0, 1.0, 40.0, 0.25, steal=0.25), **_memdisk()}),
    # user 800 (guest 800) and nice 200 (guest_nice 200): guest is inside user
    # and nice, so busy is 1000, not 2000.
    "guest-counted": (5, 40.0, {**_cpu(1.0, 1.0, 40.0, 0.25), **_memdisk()}),
    # 1000 busy, 1000 iowait, 2000 idle: iowait is total but not busy.
    "iowait": (5, 40.0, {**_cpu(1.0, 1.0, 40.0, 0.25), **_memdisk()}),
    # Utilisation of exactly 0.90 does not saturate; 0.901 does. Two intervals
    # of 3600 and two of 3604 busy: sum 14408 jiffies = 144.08 core-seconds;
    # average 14408 / 16000 * 4 = 3.602; p95 and maximum are the 3604 interval,
    # 3.604; half the intervals saturate; 144.08 / (40 s * 4) = 0.9005.
    "threshold-edge": (
        5,
        40.0,
        {
            **_cpu(3.602, 3.604, 144.08, 0.9005, p95=3.604, saturation=0.5),
            **_memdisk(),
        },
    ),
    # A garbage line and a truncated one after four good intervals are skipped.
    "truncated-tail": (
        5,
        40.0,
        {**_cpu(1.0, 1.0, 40.0, 0.25), **_memdisk()},
    ),
    # The second interval runs backwards in both busy and idle, and the third
    # loses busy time while total still rises (-500 busy, +4500 idle); both are
    # skipped. Three intervals of 1000 busy count: 30 core-seconds over
    # 30 s * 4 cores = 0.25.
    "counter-reset": (6, 50.0, {**_cpu(1.0, 1.0, 30.0, 0.25), **_memdisk()}),
}


def _record(name: str) -> dict[str, typ.Any]:
    text = (FIXTURES / f"{name}.tsv").read_text(encoding="utf-8")
    return S.build_record(
        meta=META,
        samples=S.parse_samples(text),
        env=ENV,
        end_ns=2_000_000_000_000,
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_samples_give_exact_figures(name: str) -> None:
    """Each fixture reduces to the figures worked out by hand."""
    count, wall, expected = CASES[name]
    record = _record(name)
    assert record["sample_count"] == count
    assert record["wall_seconds"] == wall
    assert {key: record[key] for key in expected} == expected


def test_core_seconds_divide_by_the_recorded_clock_tick() -> None:
    """4000 busy jiffies at 50 ticks a second is 80 core-seconds, not 40."""
    text = (FIXTURES / "one-core-of-four.tsv").read_text(encoding="utf-8")
    record = S.build_record(
        meta={**META, "clk_tck": 50},
        samples=S.parse_samples(text),
        env=ENV,
        end_ns=0,
    )
    assert record["clk_tck"] == 50
    assert record["cpu_core_seconds"] == 80.0
    assert record["cpu_efficiency"] == 0.5


def test_efficiency_uses_the_recorded_nproc_not_a_label() -> None:
    """The same jiffies on a machine presenting 8 processors: 0.125 cores' worth."""
    text = (FIXTURES / "one-core-of-four.tsv").read_text(encoding="utf-8")
    record = S.build_record(
        meta={**META, "nproc": 8},
        samples=S.parse_samples(text),
        env=ENV,
        end_ns=0,
    )
    # Utilisation 1000 / 4000 = 0.25 of 8 processors = 2.0 busy cores.
    assert record["nproc"] == 8
    assert record["cpu_busy_cores_avg"] == 2.0
    assert record["cpu_efficiency"] == 0.125


def test_single_sample_states_no_cpu_rather_than_zero() -> None:
    """One sample has no interval: CPU is null, memory and disk are real."""
    record = _record("single-sample")
    assert record["sample_count"] == 1
    assert record["wall_seconds"] == 0.0
    for key in S.CPU_KEYS:
        assert record[key] is None, key
    assert record["peak_memory_mib"] == 4000
    assert record["min_free_disk_mib"] == 20000


def test_unsupported_os_nulls_every_measurement() -> None:
    """Windows and macOS print a record with no measurements."""
    meta = {**META, "sampler": "unsupported-os"}
    text = (FIXTURES / "one-core-of-four.tsv").read_text(encoding="utf-8")
    record = S.build_record(
        meta=meta, samples=S.parse_samples(text), env=ENV, end_ns=1_090_000_000_000
    )
    assert record["sampler"] == "unsupported-os"
    for key in (*S.CPU_KEYS, *S.MEMORY_KEYS, *S.DISK_KEYS):
        assert record[key] is None, key
    assert record["wall_seconds"] == 40.0


def test_container_nulls_cpu_and_memory_but_keeps_disk() -> None:
    """In a container /proc shows the host, so only the volume is believable."""
    text = (FIXTURES / "one-core-of-four.tsv").read_text(encoding="utf-8")
    meta = {**META, "sampler": "container"}
    record = S.build_record(meta=meta, samples=S.parse_samples(text), env=ENV, end_ns=0)
    assert record["sampler"] == "container"
    for key in (*S.CPU_KEYS, *S.MEMORY_KEYS):
        assert record[key] is None, key
    assert record["min_free_disk_mib"] == 20000


def test_start_action_missing_is_stated_not_hidden() -> None:
    """With no meta file the record says so and carries no measurements."""
    record = S.build_record(meta=None, samples=[], env=ENV, end_ns=5)
    assert record["sampler"] == "not-started"
    assert record["nproc"] is None
    assert record["cpu_busy_cores_avg"] is None


def test_identity_reads_only_named_variables() -> None:
    """The secret in the environment never reaches the record."""
    record = _record("idle")
    assert record["run_id"] == 123456789
    assert record["attempt"] == 2
    assert record["repository"] == "leynos/example"
    assert record["runner_name"] == "ubicloud-standard-4-1"
    assert "hunter2" not in json.dumps(record)


def test_marker_line_is_one_line_of_the_documented_keys() -> None:
    """The marker, a space and compact JSON, with no private keys."""
    record = {**_record("idle"), "_sampler_state": "stopped"}
    line = S.marker_line(record)
    assert "\n" not in line
    marker, _, body = line.partition(" ")
    assert marker == "DF12_CI_TELEMETRY"
    parsed = json.loads(body)
    assert parsed["schema"] == 1
    assert parsed["event"] == "ci_runner_telemetry"
    assert "_sampler_state" not in parsed
    assert set(parsed) == set(EXPECTED_KEYS)


EXPECTED_KEYS = (
    "schema",
    "event",
    "sampler",
    "repository",
    "workflow",
    "job",
    "run_id",
    "attempt",
    "sha",
    "runner_name",
    "runner_os",
    "runner_arch",
    "nproc",
    "clk_tck",
    "interval_seconds",
    "sample_count",
    "wall_seconds",
    "cpu_core_seconds",
    "cpu_busy_cores_avg",
    "cpu_busy_cores_p95",
    "cpu_busy_cores_max",
    "cpu_saturation_fraction",
    "cpu_steal_fraction",
    "cpu_efficiency",
    "peak_memory_mib",
    "mem_total_mib",
    "min_free_disk_mib",
)


def test_parse_samples_skips_bad_and_non_advancing_lines() -> None:
    """Truncated, short, non-numeric and backwards-in-time lines are dropped."""
    good = "\t".join(["100", *["1"] * 13])
    later = "\t".join(["200", *["2"] * 13])
    text = "\n".join(
        [good, "garbage", "\t".join(["150", "1", "2"]), later, good, later, ""]
    )
    assert [s.t_ns for s in S.parse_samples(text)] == [100, 200]


def test_sample_columns_match_the_start_action() -> None:
    """The two actions agree on the sample file's columns."""
    from ci_telemetry_support import sampler

    assert tuple(S.SAMPLE_COLUMNS) == tuple(sampler().SAMPLE_COLUMNS)


def test_unsupported_os_wall_time_runs_from_start_to_finish() -> None:
    """With no samples at all, wall time is the start-to-finish clock."""
    meta = {**META, "sampler": "unsupported-os"}
    record = S.build_record(meta=meta, samples=[], env=ENV, end_ns=1_090_000_000_000)
    assert record["wall_seconds"] == 90.0
    assert record["sample_count"] == 0


@settings(max_examples=100, deadline=None)
@given(
    busy=st.lists(st.integers(min_value=0, max_value=4000), min_size=1, max_size=30),
    steal=st.integers(min_value=0, max_value=100),
)
def test_busy_cores_are_ordered_and_bounded(busy: list[int], steal: int) -> None:
    """Average, p95 and maximum order correctly and never exceed nproc."""
    counters = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    rows = []
    for index, jiffies in enumerate([0, *busy]):
        counters[0] += jiffies
        counters[3] += 4000 - jiffies
        counters[7] += steal if index else 0
        rows.append("\t".join(str(v) for v in [index * 10**10, *counters, 1, 1, 1]))
    record = S.build_record(
        meta=META, samples=S.parse_samples("\n".join(rows)), env=ENV, end_ns=0
    )
    avg = record["cpu_busy_cores_avg"]
    p95 = record["cpu_busy_cores_p95"]
    peak = record["cpu_busy_cores_max"]
    assert 0.0 <= avg <= peak <= META["nproc"]
    assert p95 <= peak
    assert 0.0 <= record["cpu_saturation_fraction"] <= 1.0
