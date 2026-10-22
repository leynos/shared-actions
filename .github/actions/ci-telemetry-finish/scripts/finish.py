#!/usr/bin/env python3
"""Stop the telemetry sampler and print the job's one telemetry record.

Run it last in a job under ``if: always()``. It stops the sampler by PID,
takes its own final sample so the last interval is never lost, reduces the
samples on disk with ``summarize``, and prints exactly one marker line for the
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

import sccache_stats
import summarize
import test_phase

STOP_WAIT_SECONDS = 15
CPU_FIELDS = 10
SAMPLING_KINDS = frozenset({"proc-stat", "container", "unknown-environment"})


class StateError(Exception):
    """The start action's state exists but cannot be read or is not valid."""


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


def take_sample(disk_path: str, now_ns: int | None = None) -> str:
    """Return one sample line, in the format ``ci-telemetry-start`` writes.

    ``now_ns`` replaces the monotonic clock, so a test can fix the timestamp.
    The two actions keep separate copies of this reader (each ships alone); a
    test holds this copy to the start action's columns.
    """
    cpu = _read_cpu(Path("/proc/stat").read_text(encoding="utf-8"))
    total, avail = _read_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    free = shutil.disk_usage(disk_path).free // 1024
    stamp = time.monotonic_ns() if now_ns is None else now_ns
    return "\t".join(str(v) for v in [stamp, *cpu, total, avail, free])


def _sampler_alive(pid: int) -> bool:
    """Report whether *pid* is our sampler, not a recycled process id.

    A missing ``/proc`` entry is an exited process. Any other failure to read
    it (permissions, say) is not an answer, so it propagates.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False
    return b"sampler.py" in cmdline


def _wait_for_exit(pid: int) -> bool:
    """Wait up to ``STOP_WAIT_SECONDS`` for the sampler to go; True if it did."""
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _sampler_alive(pid):
            return True
        time.sleep(0.2)
    return False


def stop_sampler(directory: Path) -> str:
    """Stop the sampler by PID and say how it ended.

    Returns
    -------
    str
        ``stopped``, ``already-exited``, ``killed``, ``no-pid`` (no usable PID
        file) or ``inspection-failed`` (the process could not be inspected or
        signalled). SIGTERM goes first and SIGKILL only after
        ``STOP_WAIT_SECONDS``; a sampler writes whole lines with one call, so
        neither leaves a half-written sample.
    """
    try:
        pid = int((directory / "sampler.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "no-pid"
    try:
        if not _sampler_alive(pid):
            return "already-exited"
        os.kill(pid, signal.SIGTERM)
        if _wait_for_exit(pid):
            return "stopped"
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    except OSError:
        return "inspection-failed"
    return "killed"


META_REQUIRED = ("sampler", "nproc", "clk_tck", "interval_seconds")


def _meta_text(directory: Path) -> str | None:
    """Return ``meta.json``'s text; None when it is absent.

    Raises
    ------
    StateError
        If the file exists but cannot be read.
    """
    try:
        return (directory / "meta.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        message = f"meta.json is unreadable: {exc}"
        raise StateError(message) from exc


def _parse_meta(text: str) -> dict[str, typ.Any]:
    """Return the parsed metadata, or raise ``StateError`` if it is not valid."""
    try:
        meta = json.loads(text)
    except ValueError as exc:
        message = "meta.json is not valid JSON"
        raise StateError(message) from exc
    if not isinstance(meta, dict) or any(key not in meta for key in META_REQUIRED):
        message = "meta.json lacks the fields the record needs"
        raise StateError(message)
    return meta


def read_meta(directory: Path) -> dict[str, typ.Any] | None:
    """Return ``meta.json``; None when the start action never wrote it.

    Raises
    ------
    StateError
        If the file exists but cannot be read, is not JSON, or lacks the
        fields the record needs.
    """
    text = _meta_text(directory)
    return None if text is None else _parse_meta(text)


def _final_sample(directory: Path, meta: typ.Mapping[str, typ.Any]) -> None:
    """Append finish's own sample, so the last interval is never lost."""
    try:
        line = take_sample(str(meta.get("disk_path") or "."))
        with (directory / "samples.tsv").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except (OSError, ValueError, KeyError) as exc:
        print(f"::warning title=ci-telemetry::final sample failed: {exc}")


def _read_samples(directory: Path) -> list[summarize.Sample]:
    """Return the samples on disk; unreadable bytes are skipped, not fatal."""
    try:
        text = (directory / "samples.tsv").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return summarize.parse_samples(text)


def _measurements(directory: Path, env: typ.Mapping[str, str]) -> dict[str, typ.Any]:
    """Stop the sampler, sample once more and return the CPU, memory and disk record.

    Unreadable or invalid state gives a record whose ``sampler`` is
    ``state-invalid``, so the degraded job is visible rather than hidden.
    """
    try:
        meta = read_meta(directory)
    except StateError as exc:
        print(f"::warning title=ci-telemetry::{exc}")
        record = summarize.build_record(
            meta=None, samples=[], env=env, end_ns=time.monotonic_ns()
        )
        return {**record, "sampler": "state-invalid", "_sampler_state": "n/a"}
    state = "no-meta"
    if meta is not None:
        kind = meta["sampler"]
        state = "n/a" if kind == "unsupported-os" else stop_sampler(directory)
        if kind in SAMPLING_KINDS:
            _final_sample(directory, meta)
    record = summarize.build_record(
        meta=meta,
        samples=_read_samples(directory),
        env=env,
        end_ns=time.monotonic_ns(),
    )
    return {**record, "_sampler_state": state}


def finish(directory: Path, env: typ.Mapping[str, str]) -> dict[str, typ.Any]:
    """Return the job's record: measurements plus the normalized sccache fields.

    ``CT_SCCACHE_STATUS`` is setup-rust's ``sccache-status`` output and
    ``CT_SCCACHE_STATS_FILE`` the file ``sccache-report`` wrote; the statistics
    are read from that file when it exists, and from sccache itself otherwise.
    """
    sccache = sccache_stats.collect(
        env,
        env.get("CT_SCCACHE_STATUS", ""),
        env.get("CT_SCCACHE_STATS_FILE", ""),
    )
    record = {**_measurements(directory, env), **sccache}
    build_file = env.get("CT_SCCACHE_BUILD_STATS_FILE", "")
    record.update(
        test_phase.collect(sccache, lambda: sccache_stats.read_stats_file(build_file))
    )
    record.update(test_phase.judge(record, env))
    return record


def degraded_record(env: typ.Mapping[str, str], exc: Exception) -> dict[str, typ.Any]:
    """Return a record that says telemetry failed, and with what."""
    return {
        "schema": summarize.SCHEMA,
        "event": summarize.EVENT,
        "sampler": "error",
        **summarize.identity(env),
        "error": type(exc).__name__,
        "_sampler_state": "n/a",
    }


def print_marker(record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]) -> None:
    """Print the one marker line."""
    if record["sampler"] == "not-started":
        print(
            "::warning title=ci-telemetry::ci-telemetry-start did not run in "
            "this job; the record carries no measurements"
        )
    print(summarize.marker_line(record))
    for line in test_phase.annotations(record, env):
        print(line)


def print_summary(record: typ.Mapping[str, typ.Any]) -> None:
    """Print the human summary table."""
    print("ci-telemetry summary")
    for measure, value in summarize.summary_rows(record):
        print(f"  {measure:<28} {value}")
    print(f"  {'Sampler ended':<28} {record.get('_sampler_state', '')}")


def write_record(record: typ.Mapping[str, typ.Any], directory: Path) -> Path:
    """Write the record's JSON, without the marker, and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "record.json"
    body = summarize.marker_line(record).split(" ", 1)[1]
    path.write_text(body + "\n", encoding="utf-8")
    return path


def write_job_summary(
    record: typ.Mapping[str, typ.Any], env: typ.Mapping[str, str]
) -> None:
    """Append the summary table to ``GITHUB_STEP_SUMMARY`` when it is set."""
    target = env.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(f"### CI telemetry ({env.get('GITHUB_JOB', 'job')})\n\n")
        handle.write("| Measure | Value |\n| --- | --- |\n")
        handle.writelines(f"| {m} | {v} |\n" for m, v in summarize.summary_rows(record))


def write_outputs(
    record: typ.Mapping[str, typ.Any], path: Path, env: typ.Mapping[str, str]
) -> None:
    """Append the action's outputs to ``GITHUB_OUTPUT`` when it is set."""
    target = env.get("GITHUB_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(f"record-path={path}\nsampler={record['sampler']}\n")


def emit(
    record: typ.Mapping[str, typ.Any], directory: Path, env: typ.Mapping[str, str]
) -> None:
    """Print the marker and summary, then write the file, summary and outputs.

    The marker goes first and cannot fail; each later step reports its own
    failure and the rest still run.
    """
    print_marker(record, env)
    print_summary(record)
    path = directory / "record.json"
    for label, step in (
        ("record file", lambda: write_record(record, directory)),
        ("job summary", lambda: write_job_summary(record, env)),
    ):
        try:
            step()
        except OSError as exc:
            print(f"::warning title=ci-telemetry::{label} not written: {exc}")
    try:
        write_outputs(record, path, env)
    except OSError as exc:
        print(f"::warning title=ci-telemetry::outputs not written: {exc}")


def main() -> int:
    """Finish the job's telemetry; always exits 0."""
    directory = state_dir()
    try:
        record = finish(directory, os.environ)
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a job
        print(f"::warning title=ci-telemetry::telemetry failed: {exc!r}")
        record = degraded_record(os.environ, exc)
    emit(record, directory, os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
