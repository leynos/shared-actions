"""Reduce raw telemetry samples to one record: a pure function over a file.

``ci-telemetry-start`` appends raw ``/proc/stat``, memory and disk samples;
this module turns them into the figures a right-sizing decision needs. Nothing
here reads the machine, so tests give it recorded samples and compare exact
figures.

CPU time follows Linux's own accounting:

* busy is ``user + nice + system + irq + softirq``;
* ``steal`` is time the job wanted and the hypervisor withheld. It is not busy,
  and it is reported separately as a fraction of all CPU time;
* ``guest`` and ``guest_nice`` are already counted inside ``user`` and
  ``nice``, so adding them would count that time twice;
* total is busy plus ``idle``, ``iowait`` and ``steal``.

Figures come from the deltas between consecutive samples. An interval whose
counters did not advance, or went backwards, is skipped, and efficiency is
computed over the intervals that counted.
"""

from __future__ import annotations

import dataclasses as dc
import itertools
import json
import math
import typing as typ

SCHEMA = 1
EVENT = "ci_runner_telemetry"
#: The marker is built, not written out, so it never appears verbatim in the
#: echoed command or in this action's manifest.
MARKER = "DF12_" + "CI_TELEMETRY"
SATURATION_THRESHOLD = 0.90
P95 = 0.95
KIB_PER_MIB = 1024
NS_PER_SECOND = 1_000_000_000

SAMPLE_COLUMNS = (
    "t_ns",
    "user",
    "nice",
    "system",
    "idle",
    "iowait",
    "irq",
    "softirq",
    "steal",
    "guest",
    "guest_nice",
    "mem_total_kib",
    "mem_available_kib",
    "free_disk_kib",
)

CPU_KEYS = (
    "cpu_core_seconds",
    "cpu_busy_cores_avg",
    "cpu_busy_cores_p95",
    "cpu_busy_cores_max",
    "cpu_saturation_fraction",
    "cpu_steal_fraction",
    "cpu_efficiency",
)
MEMORY_KEYS = ("peak_memory_mib", "mem_total_mib")
DISK_KEYS = ("min_free_disk_mib",)

IDENTITY_ENV = (
    ("repository", "GITHUB_REPOSITORY"),
    ("workflow", "GITHUB_WORKFLOW"),
    ("job", "GITHUB_JOB"),
    ("run_id", "GITHUB_RUN_ID"),
    ("attempt", "GITHUB_RUN_ATTEMPT"),
    ("sha", "GITHUB_SHA"),
    ("runner_name", "RUNNER_NAME"),
    ("runner_os", "RUNNER_OS"),
    ("runner_arch", "RUNNER_ARCH"),
)
INTEGER_IDENTITY = frozenset({"run_id", "attempt"})


@dc.dataclass(frozen=True)
class Sample:
    """One raw reading: a timestamp, ten CPU counters, memory and free disk."""

    t_ns: int
    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int
    guest: int
    guest_nice: int
    mem_total_kib: int
    mem_available_kib: int
    free_disk_kib: int

    @property
    def busy(self) -> int:
        """Jiffies spent working: steal excluded, guest not added."""
        return self.user + self.nice + self.system + self.irq + self.softirq

    @property
    def total(self) -> int:
        """Jiffies of all CPU time: busy, idle, iowait and steal."""
        return self.busy + self.idle + self.iowait + self.steal


def parse_samples(text: str) -> list[Sample]:
    """Return the well-formed samples in *text*, in order.

    A line that is truncated (a sampler killed mid-write), has the wrong number
    of columns or is not numeric is skipped, as is any line whose timestamp does
    not advance past the previous accepted one.
    """
    samples: list[Sample] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != len(SAMPLE_COLUMNS):
            continue
        try:
            sample = Sample(*(int(part) for part in parts))
        except ValueError:
            continue
        if samples and sample.t_ns <= samples[-1].t_ns:
            continue
        samples.append(sample)
    return samples


def nearest_rank(values: list[float], fraction: float) -> float:
    """Return the nearest-rank percentile of *values* (non-empty)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


@dc.dataclass(frozen=True)
class Cpu:
    """The CPU figures of a run, before rounding."""

    core_seconds: float
    avg: float
    p95: float
    peak: float
    saturation: float
    steal: float
    efficiency: float


def cpu_figures(samples: list[Sample], nproc: int, clk_tck: int) -> Cpu | None:
    """Reduce consecutive sample pairs to the CPU figures, or None.

    Returns None when no interval advanced the counters (fewer than two
    samples, or a frozen clock), since no utilisation can be stated then.
    """
    utilisation: list[float] = []
    busy_sum = total_sum = steal_sum = valid_ns = 0
    for before, after in itertools.pairwise(samples):
        d_busy = after.busy - before.busy
        d_total = after.total - before.total
        if d_total <= 0 or d_busy < 0:
            continue
        utilisation.append(d_busy / d_total)
        busy_sum += d_busy
        total_sum += d_total
        steal_sum += after.steal - before.steal
        valid_ns += after.t_ns - before.t_ns
    if not utilisation:
        return None
    wall = valid_ns / NS_PER_SECOND
    core_seconds = busy_sum / clk_tck
    return Cpu(
        core_seconds=core_seconds,
        avg=busy_sum / total_sum * nproc,
        p95=nearest_rank(utilisation, P95) * nproc,
        peak=max(utilisation) * nproc,
        saturation=sum(1 for u in utilisation if u > SATURATION_THRESHOLD)
        / len(utilisation),
        steal=steal_sum / total_sum,
        efficiency=core_seconds / (wall * nproc),
    )


def _identity_value(key: str, value: str | None) -> str | int | None:
    """Return *value*, as an integer for the keys that are integers."""
    if value and key in INTEGER_IDENTITY and value.isdigit():
        return int(value)
    return value or None


def identity(env: typ.Mapping[str, str]) -> dict[str, typ.Any]:
    """Return the record's identity fields from the runner's ``GITHUB_*`` env.

    Only these named variables are read, so no secret can reach the record.
    ``run_id`` and ``attempt`` are integers when they look like integers.
    """
    return {
        key: _identity_value(key, env.get(variable)) for key, variable in IDENTITY_ENV
    }


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


def _cpu_fields(cpu: Cpu | None) -> dict[str, float | None]:
    """Return the rounded CPU fields, all None when there is no figure."""
    if cpu is None:
        return dict.fromkeys(CPU_KEYS)
    return {
        "cpu_core_seconds": _round(cpu.core_seconds, 3),
        "cpu_busy_cores_avg": _round(cpu.avg, 4),
        "cpu_busy_cores_p95": _round(cpu.p95, 4),
        "cpu_busy_cores_max": _round(cpu.peak, 4),
        "cpu_saturation_fraction": _round(cpu.saturation, 4),
        "cpu_steal_fraction": _round(cpu.steal, 4),
        "cpu_efficiency": _round(cpu.efficiency, 4),
    }


def _memory_fields(samples: list[Sample]) -> dict[str, int | None]:
    """Return peak and total memory in MiB, None without samples."""
    if not samples:
        return dict.fromkeys(MEMORY_KEYS)
    used = max(s.mem_total_kib - s.mem_available_kib for s in samples)
    return {
        "peak_memory_mib": used // KIB_PER_MIB,
        "mem_total_mib": samples[0].mem_total_kib // KIB_PER_MIB,
    }


def _disk_fields(samples: list[Sample]) -> dict[str, int | None]:
    """Return the minimum free disk in MiB, None without samples."""
    if not samples:
        return dict.fromkeys(DISK_KEYS)
    return {"min_free_disk_mib": min(s.free_disk_kib for s in samples) // KIB_PER_MIB}


def _wall_seconds(
    meta: typ.Mapping[str, typ.Any] | None, samples: list[Sample], end_ns: int
) -> float | None:
    """Return the sampled span, or start-to-finish when nothing was sampled."""
    if samples:
        return _round((samples[-1].t_ns - samples[0].t_ns) / NS_PER_SECOND, 3)
    started = (meta or {}).get("started_ns")
    return None if started is None else _round((end_ns - started) / NS_PER_SECOND, 3)


def _shape(meta: typ.Mapping[str, typ.Any] | None) -> dict[str, typ.Any]:
    """Return the sampler kind and the machine's shape from ``meta.json``."""
    if meta is None:
        return {
            "sampler": "not-started",
            "nproc": None,
            "clk_tck": None,
            "interval_seconds": None,
        }
    return {
        "sampler": str(meta["sampler"]),
        "nproc": int(meta["nproc"]),
        "clk_tck": int(meta["clk_tck"]),
        "interval_seconds": meta["interval_seconds"],
    }


def build_record(
    *,
    meta: typ.Mapping[str, typ.Any] | None,
    samples: list[Sample],
    env: typ.Mapping[str, str],
    end_ns: int,
) -> dict[str, typ.Any]:
    """Build the telemetry record.

    Parameters
    ----------
    meta : Mapping | None
        The start action's ``meta.json``, or None when it never ran.
    samples : list[Sample]
        Every sample on disk, including the finish action's own final one.
    env : Mapping[str, str]
        The environment to read identity fields from.
    end_ns : int
        The monotonic clock at finish, for jobs that took no samples.

    Returns
    -------
    dict
        The record. CPU fields are None unless ``sampler`` is ``proc-stat``;
        memory and CPU are None in a container, where ``/proc`` shows the host.
    """
    shape = _shape(meta)
    kind = shape["sampler"]
    measured = kind == "proc-stat"
    cpu = cpu_figures(samples, shape["nproc"], shape["clk_tck"]) if measured else None
    on_disk = kind in {"proc-stat", "container"}
    return {
        "schema": SCHEMA,
        "event": EVENT,
        **identity(env),
        **shape,
        "sample_count": len(samples),
        "wall_seconds": _wall_seconds(meta, samples, end_ns),
        **_cpu_fields(cpu),
        **_memory_fields(samples if measured else []),
        **_disk_fields(samples if on_disk else []),
    }


def marker_line(record: typ.Mapping[str, typ.Any]) -> str:
    """Return the single marker line: the marker, a space, compact JSON.

    Keys beginning with an underscore are for the human summary and stay out
    of the line. The JSON escapes line breaks, so the result is one line.
    """
    public = {k: v for k, v in record.items() if not k.startswith("_")}
    body = json.dumps(public, separators=(",", ":"), sort_keys=True)
    return f"{MARKER} {body}"


def summary_rows(record: typ.Mapping[str, typ.Any]) -> list[tuple[str, str]]:
    """Return the human summary as ``(measure, value)`` rows."""

    def show(key: str, unit: str = "") -> str:
        value = record.get(key)
        return "n/a" if value is None else f"{value}{unit}"

    return [
        ("Sampler", show("sampler")),
        ("Processors (nproc)", show("nproc")),
        ("Samples", show("sample_count")),
        ("Wall time", show("wall_seconds", " s")),
        ("CPU busy cores, average", show("cpu_busy_cores_avg")),
        ("CPU busy cores, p95", show("cpu_busy_cores_p95")),
        ("CPU busy cores, maximum", show("cpu_busy_cores_max")),
        ("CPU efficiency", show("cpu_efficiency")),
        ("CPU saturation (> 90%)", show("cpu_saturation_fraction")),
        ("CPU steal fraction", show("cpu_steal_fraction")),
        ("Core-seconds used", show("cpu_core_seconds")),
        ("Peak memory used", show("peak_memory_mib", " MiB")),
        ("Minimum free disk", show("min_free_disk_mib", " MiB")),
    ]
