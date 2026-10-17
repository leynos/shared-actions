#!/usr/bin/env python3
"""Sample CPU, memory and disk while a CI job runs.

``start`` records the machine's shape, takes a first sample and detaches a
background loop that appends one raw sample line every interval. It computes
nothing: ``ci-telemetry-finish`` reduces the samples to a record, so the
arithmetic is a pure function over a file that tests can feed recorded data.

The sample file is the contract between the two actions. Each line is
``SAMPLE_COLUMNS`` joined by tabs: a monotonic timestamp in nanoseconds, the
ten counters of the aggregate ``cpu`` line of ``/proc/stat`` in jiffies, then
``MemTotal`` and ``MemAvailable`` in KiB and the free space of the watched
volume in KiB. ``ci-telemetry-finish`` holds a copy of the column list, and a
test in each action holds the two equal.

Only the standard library is used: the sampler must start on a runner before
any tool is installed. It wakes every ``CT_INTERVAL_SECONDS`` (default 10) and
reads three small files, which is negligible for a compile job and
unacceptable for one whose output is a timing; do not run it on a benchmark.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

FORMAT = 1
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
CPU_FIELDS = 10
DEFAULT_INTERVAL = 10
SAMPLER = "proc-stat"
UNSUPPORTED_OS = "unsupported-os"
CONTAINER = "container"
UNKNOWN = "unknown-environment"
CONTAINER_MARKERS = ("docker", "containerd", "kubepods", "lxc", "podman")


def state_dir() -> Path:
    """Return the directory holding the sampler's files."""
    return Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "ci-telemetry"  # noqa: S108


def in_container(root: Path = Path("/")) -> bool:
    """Report whether the job runs inside a container.

    Parameters
    ----------
    root : Path
        The filesystem root to inspect; tests point it at a fixture tree.

    Returns
    -------
    bool
        True when ``/.dockerenv`` exists or the init cgroup names a container
        runtime. A container job reads the host's ``/proc/stat``, which is not
        the job's own CPU. A missing cgroup file means no container; any other
        failure to read it propagates, so the caller can say it does not know.
    """
    if (root / ".dockerenv").exists():
        return True
    try:
        cgroup = (root / "proc/1/cgroup").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return any(marker in cgroup for marker in CONTAINER_MARKERS)


def classify(system: str, root: Path = Path("/")) -> str:
    """Return the sampler kind for this machine.

    Parameters
    ----------
    system : str
        ``platform.system()``.
    root : Path
        The filesystem root to inspect for container markers.

    Returns
    -------
    str
        ``proc-stat``, ``unsupported-os``, ``container`` or
        ``unknown-environment`` when the container probe could not read its
        files (so the host's counters are not passed off as the job's).
    """
    if system != "Linux":
        return UNSUPPORTED_OS
    try:
        return CONTAINER if in_container(root) else SAMPLER
    except OSError as exc:
        print(f"::warning title=ci-telemetry::container probe failed: {exc}")
        return UNKNOWN


def parse_cpu_line(text: str) -> list[int]:
    """Return the ten counters of the aggregate ``cpu`` line.

    Parameters
    ----------
    text : str
        The contents of ``/proc/stat``.

    Returns
    -------
    list[int]
        user, nice, system, idle, iowait, irq, softirq, steal, guest and
        guest_nice. Kernels before 2.6.33 omit trailing fields, which read as
        zero.

    Raises
    ------
    ValueError
        If there is no aggregate ``cpu`` line.
    """
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(part) for part in parts[1 : 1 + CPU_FIELDS]]
            return values + [0] * (CPU_FIELDS - len(values))
    message = "no aggregate cpu line in /proc/stat"
    raise ValueError(message)


def parse_meminfo(text: str) -> tuple[int, int]:
    """Return ``(MemTotal, MemAvailable)`` in KiB from ``/proc/meminfo``.

    Memory in use is total minus available, not total minus free: page cache is
    reclaimable, and counting it as used would report every job as near its
    limit.
    """
    found: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            found[key] = int(rest.split()[0])
    return found["MemTotal"], found["MemAvailable"]


def take_sample(disk_path: str, now_ns: int | None = None) -> str:
    """Read the machine's counters and return one sample line.

    Parameters
    ----------
    disk_path : str
        The directory whose volume's free space is recorded.
    now_ns : int | None
        A timestamp to use instead of the monotonic clock, for tests.

    Returns
    -------
    str
        The tab-separated line, without a trailing newline.
    """
    cpu = parse_cpu_line(Path("/proc/stat").read_text(encoding="utf-8"))
    total, available = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    free = shutil.disk_usage(disk_path).free // 1024
    stamp = time.monotonic_ns() if now_ns is None else now_ns
    values = [stamp, *cpu, total, available, free]
    if len(values) != len(SAMPLE_COLUMNS):
        message = f"sample has {len(values)} values for {len(SAMPLE_COLUMNS)} columns"
        raise ValueError(message)
    return "\t".join(str(v) for v in values)


def append_line(path: Path, line: str) -> None:
    """Append one line with a single write, so a reader never sees half of it."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def nproc() -> int:
    """Return the processors this process may use, as ``nproc`` does."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def clk_tck() -> int:
    """Return the kernel's jiffies per second, or 100 where it is unknown."""
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


def _write_meta(directory: Path, kind: str, interval: int, disk_path: str) -> None:
    """Record the machine's shape for the finish action."""
    meta = {
        "format": FORMAT,
        "sampler": kind,
        "nproc": nproc(),
        "clk_tck": clk_tck(),
        "interval_seconds": interval,
        "disk_path": disk_path,
        "started_ns": time.monotonic_ns(),
        "runner_os": platform.system(),
    }
    (directory / "meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")


def _first_sample(directory: Path, disk_path: str) -> None:
    """Take the first sample; a failure is a warning, not the end of sampling."""
    try:
        append_line(directory / "samples.tsv", take_sample(disk_path))
    except (OSError, ValueError, KeyError) as exc:
        print(f"::warning title=ci-telemetry::first sample failed: {exc}")


def _spawn_loop(interval: int) -> subprocess.Popen[bytes] | None:
    """Detach the sampling loop, or return None after a warning."""
    try:
        return subprocess.Popen(  # noqa: S603 - our own interpreter and script
            [sys.executable, str(Path(__file__).resolve()), "run"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "CT_INTERVAL_SECONDS": str(interval)},
        )
    except OSError as exc:
        print(f"::warning title=ci-telemetry::sampler did not start: {exc}")
        return None


def start(interval: int, disk_path: str) -> int:
    """Record the machine's shape, sample once and detach the sampling loop.

    Returns
    -------
    int
        The process exit status: always 0, since telemetry never fails a job.
    """
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("samples.tsv", "sampler.pid", "record.json"):
        (directory / name).unlink(missing_ok=True)
    kind = classify(platform.system())
    _write_meta(directory, kind, interval, disk_path)
    if kind == UNSUPPORTED_OS:
        print(f"ci-telemetry: {platform.system()} has no /proc; no samples taken")
        return 0
    _first_sample(directory, disk_path)
    child = _spawn_loop(interval)
    if child is None:
        return 0
    (directory / "sampler.pid").write_text(f"{child.pid}\n", encoding="utf-8")
    print(
        f"ci-telemetry: {kind} sampler started, pid {child.pid}, "
        f"interval {interval}s, state {directory}"
    )
    return 0


def run_loop(interval: int, disk_path: str) -> None:
    """Append a sample every *interval* seconds until killed."""
    samples = state_dir() / "samples.tsv"
    while True:
        time.sleep(interval)
        try:
            append_line(samples, take_sample(disk_path))
        except (OSError, ValueError, KeyError):
            continue


def _interval() -> int:
    """Return the sampling interval from the environment, at least 1 second."""
    try:
        return max(1, int(os.environ.get("CT_INTERVAL_SECONDS", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


def main(argv: list[str]) -> int:
    """Run the ``start`` or ``run`` command."""
    command = argv[1] if len(argv) > 1 else ""
    disk_path = os.environ.get("CT_DISK_PATH") or "."
    if command == "start":
        return start(_interval(), disk_path)
    if command == "run":
        run_loop(_interval(), disk_path)
        return 0
    print(f"usage: {Path(argv[0]).name} {{start|run}}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
