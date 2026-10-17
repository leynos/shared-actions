#!/usr/bin/env python3
"""Stop the telemetry sampler and print the job's one telemetry record.

Run it last in a job under ``if: always()``. It stops the sampler by PID,
takes its own final sample so the last interval is never lost, reduces the
samples on disk with ``summarise``, and prints exactly one marker line for the
central collector plus a human summary. The runner's orphan-process cleanup
runs after a job's steps, so the sampler may already be gone: the record is
built from the samples on disk either way. Telemetry never fails a job.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
import typing as typ
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import summarise

STOP_WAIT_SECONDS = 15
CPU_FIELDS = 10


def state_dir() -> Path:
    """Return the directory ``ci-telemetry-start`` wrote to."""
    return Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "ci-telemetry"  # noqa: S108


def _read_cpu(text: str) -> list[int]:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(part) for part in parts[1 : 1 + CPU_FIELDS]]
            return values + [0] * (CPU_FIELDS - len(values))
    message = "no aggregate cpu line in /proc/stat"
    raise ValueError(message)


def _read_meminfo(text: str) -> tuple[int, int]:
    found: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            found[key] = int(rest.split()[0])
    return found["MemTotal"], found["MemAvailable"]


def take_sample(disk_path: str) -> str:
    """Return one sample line, in the format ``ci-telemetry-start`` writes."""
    cpu = _read_cpu(Path("/proc/stat").read_text(encoding="utf-8"))
    total, avail = _read_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    free = shutil.disk_usage(disk_path).free // 1024
    return "\t".join(str(v) for v in [time.monotonic_ns(), *cpu, total, avail, free])


def _is_sampler(pid: int) -> bool:
    """Report whether *pid* is our sampler, not a recycled process id."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"sampler.py" in cmdline


def stop_sampler(directory: Path) -> str:
    """Stop the sampler by PID and say how it ended.

    Returns
    -------
    str
        ``stopped``, ``already-exited``, ``killed`` or ``no-pid``. SIGTERM goes
        first and SIGKILL only after ``STOP_WAIT_SECONDS``; a sampler writes
        whole lines with one call, so neither leaves a half-written sample.
    """
    try:
        pid = int((directory / "sampler.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "no-pid"
    if not _is_sampler(pid):
        return "already-exited"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "already-exited"
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _is_sampler(pid):
            return "stopped"
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return "stopped"
    return "killed"


def read_meta(directory: Path) -> dict[str, typ.Any] | None:
    """Return ``meta.json`` or None when the start action never ran."""
    try:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and "sampler" in meta else None


def finish(directory: Path, env: typ.Mapping[str, str]) -> dict[str, typ.Any]:
    """Stop the sampler, sample once more and return the record."""
    meta = read_meta(directory)
    samples_path = directory / "samples.tsv"
    state = "no-meta"
    if meta is not None:
        state = (
            stop_sampler(directory) if meta["sampler"] != "unsupported-os" else "n/a"
        )
        if meta["sampler"] in {"proc-stat", "container"}:
            try:
                line = take_sample(str(meta.get("disk_path") or "."))
                with samples_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except (OSError, ValueError, KeyError) as exc:
                print(f"::warning title=ci-telemetry::final sample failed: {exc}")
    text = samples_path.read_text(encoding="utf-8") if samples_path.exists() else ""
    return summarise.build_record(
        meta=meta,
        samples=summarise.parse_samples(text),
        env=env,
        end_ns=time.monotonic_ns(),
        sampler_state=state,
    )


def emit(
    record: typ.Mapping[str, typ.Any], directory: Path, env: typ.Mapping[str, str]
) -> None:
    """Print the marker line and the summary, write the record and outputs."""
    if record["sampler"] == "not-started":
        print(
            "::warning title=ci-telemetry::ci-telemetry-start did not run in "
            "this job; the record carries no measurements"
        )
    print(summarise.marker_line(record))
    rows = summarise.summary_rows(record)
    print("ci-telemetry summary")
    for measure, value in rows:
        print(f"  {measure:<28} {value}")
    print(f"  {'Sampler ended':<28} {record.get('_sampler_state', '')}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "record.json"
    path.write_text(
        summarise.marker_line(record).split(" ", 1)[1] + "\n", encoding="utf-8"
    )
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"### CI telemetry ({env.get('GITHUB_JOB', 'job')})\n\n")
            handle.write("| Measure | Value |\n| --- | --- |\n")
            handle.writelines(f"| {m} | {v} |\n" for m, v in rows)
    output = env.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"record-path={path}\nsampler={record['sampler']}\n")


def main() -> int:
    """Finish the job's telemetry; always exits 0."""
    directory = state_dir()
    try:
        record = finish(directory, os.environ)
        emit(record, directory, os.environ)
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a job
        print(f"::warning title=ci-telemetry::telemetry failed: {type(exc).__name__}")
        del exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
