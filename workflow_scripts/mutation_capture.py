#!/usr/bin/env python3
"""Capture bounded stdout and stderr around a mutation workflow command.

The wrapper retains the first bytes of each stream, preserves the command's
exit status, and writes ``stdout.log``, ``stderr.log`` and ``metadata.json``.
It uses only the standard library so it can still run when ``uv`` itself fails
to start; using ``uv run --script`` here would depend on the tool this wrapper
needs to diagnose.

Examples
--------
Capture a command::

    python3 mutation_capture.py --output-dir diagnostics -- uv run --script x.py

Verify evidence after a failed step::

    python3 mutation_capture.py --output-dir diagnostics --verify-failure
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import subprocess
import sys
import threading
import time
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from _thread import LockType

DEFAULT_MAX_BYTES_PER_STREAM = 512 * 1024
PIPE_DRAIN_GRACE_SECONDS = 5.0


@dataclasses.dataclass
class _CaptureEvidence:
    """Mutable counters and files for one command's captured output."""

    max_bytes_per_stream: int
    stream_files: dict[str, typ.BinaryIO]
    counts: dict[str, dict[str, int]] = dataclasses.field(init=False)
    capture_errors: list[str] = dataclasses.field(default_factory=list)
    _state_lock: LockType = dataclasses.field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _accepting_output: bool = dataclasses.field(default=True, init=False, repr=False)
    _capture_incomplete: bool = dataclasses.field(default=False, init=False, repr=False)
    _metadata_finalized: bool = dataclasses.field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize independent byte counters for both captured streams."""
        self.counts = {
            name: {"bytes_seen": 0, "bytes_saved": 0, "truncated_bytes": 0}
            for name in ("stdout", "stderr")
        }

    def _record_chunk(self, stream_name: str, chunk: bytes) -> None:
        """Retain a stream prefix and update the corresponding byte counts."""
        capture_error: str | None = None
        with self._state_lock:
            if not self._accepting_output:
                return
            stream_count = self.counts[stream_name]
            stream_count["bytes_seen"] += len(chunk)
            remaining = self.max_bytes_per_stream - stream_count["bytes_saved"]
            retained = chunk[:remaining] if remaining > 0 else b""
            if retained:
                try:
                    self.stream_files[stream_name].write(retained)
                    self.stream_files[stream_name].flush()
                    stream_count["bytes_saved"] += len(retained)
                except (OSError, ValueError) as error:
                    capture_error = f"could not save {stream_name} diagnostics: {error}"
            stream_count["truncated_bytes"] = (
                stream_count["bytes_seen"] - stream_count["bytes_saved"]
            )
        if capture_error is not None:
            self._record_error(capture_error)

    def _forward_output(self, stream_name: str, chunk: bytes) -> None:
        """Keep the original command output visible in the Actions step log."""
        with self._state_lock:
            if not self._accepting_output:
                return
        target = sys.stdout.buffer if stream_name == "stdout" else sys.stderr.buffer
        try:
            target.write(chunk)
            target.flush()
        except (BrokenPipeError, OSError) as error:
            self._record_error(
                f"could not forward {stream_name} to the workflow log: {error}"
            )

    def _to_metadata(
        self, *, state: str, exit_code: int | None, process_returncode: int | None
    ) -> dict[str, object]:
        """Build the versioned evidence envelope for both streams."""
        with self._state_lock:
            final_state = "incomplete" if self._capture_incomplete else state
            return {
                "capture_errors": list(self.capture_errors),
                "exit_code": exit_code,
                "max_bytes_per_stream": self.max_bytes_per_stream,
                "process_returncode": process_returncode,
                "retention": "Stream prefixes are kept; later bytes are discarded.",
                "state": final_state,
                "stderr": dict(self.counts["stderr"]),
                "stdout": dict(self.counts["stdout"]),
            }

    def _finalize(self) -> None:
        """Stop accepting output and freeze the recorded capture errors."""
        with self._state_lock:
            self._accepting_output = False
            self._metadata_finalized = True

    def _record_error(self, message: str) -> None:
        """Record a capture error once, safely across both stream readers."""
        with self._state_lock:
            if self._metadata_finalized:
                return
            if message not in self.capture_errors:
                self.capture_errors.append(message)

    @property
    def _has_errors(self) -> bool:
        """Return whether any part of diagnostic capture has failed."""
        with self._state_lock:
            return bool(self.capture_errors)

    def _mark_incomplete(self, message: str) -> None:
        """Stop accepting bytes and record that a pipe did not reach EOF."""
        with self._state_lock:
            self._accepting_output = False
            self._capture_incomplete = True
            if not self._metadata_finalized and message not in self.capture_errors:
                self.capture_errors.append(message)


def _write_metadata(path: Path, metadata: dict[str, object]) -> None:
    """Atomically replace capture metadata so readers never see partial JSON."""
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def _capture_stream_output(
    stream_name: str,
    stream: typ.IO[bytes],
    evidence: _CaptureEvidence,
) -> None:
    """Drain one process pipe while retaining and forwarding its bytes."""
    try:
        while chunk := stream.read(16 * 1024):
            evidence._record_chunk(stream_name, chunk)
            evidence._forward_output(stream_name, chunk)
    except (OSError, ValueError) as error:
        evidence._record_error(f"could not read {stream_name} diagnostics: {error}")
    finally:
        try:
            stream.close()
        except (OSError, ValueError) as error:
            evidence._record_error(
                f"could not close {stream_name} output pipe: {error}"
            )


_OutputReader = tuple[threading.Thread, typ.IO[bytes]]


def _start_output_readers(
    stdout: typ.IO[bytes],
    stderr: typ.IO[bytes],
    evidence: _CaptureEvidence,
) -> tuple[_OutputReader, _OutputReader]:
    """Start daemon readers for both subprocess output pipes."""
    readers = (
        (
            threading.Thread(
                target=_capture_stream_output,
                args=("stdout", stdout, evidence),
                name="capture-stdout",
                daemon=True,
            ),
            stdout,
        ),
        (
            threading.Thread(
                target=_capture_stream_output,
                args=("stderr", stderr, evidence),
                name="capture-stderr",
                daemon=True,
            ),
            stderr,
        ),
    )
    for thread, _stream in readers:
        thread.start()
    return readers


def _join_output_readers(readers: tuple[_OutputReader, ...]) -> None:
    """Wait for both readers against one shared EOF deadline."""
    deadline = time.monotonic() + PIPE_DRAIN_GRACE_SECONDS
    for thread, _stream in readers:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _abandon_unfinished_readers(
    readers: tuple[_OutputReader, ...], evidence: _CaptureEvidence
) -> None:
    """Mark inherited pipes incomplete without blocking on cross-thread close."""
    for thread, _stream in readers:
        if thread.is_alive():
            evidence._mark_incomplete(
                f"{thread.name} did not reach EOF after the command exited; "
                "a descendant may still hold the pipe"
            )

    # Closing a pipe from a second thread can wait on its blocked read. These
    # readers are daemon threads; they can finish naturally after wrapper exit.


def _capture_process_output(
    process: subprocess.Popen[bytes], evidence: _CaptureEvidence
) -> int:
    """Drain stdout and stderr concurrently without blocking full pipes."""
    if process.stdout is None or process.stderr is None:
        evidence._record_error("the mutation process did not provide both output pipes")
        process.kill()
        return process.wait()

    # Windows selectors cannot monitor subprocess pipes, so drain them in threads.
    readers = _start_output_readers(process.stdout, process.stderr, evidence)

    # These threads keep draining while wait() blocks, so full pipes cannot
    # block the process. A descendant may keep the write handles open after
    # the direct process exits, so EOF has its own bounded grace period.
    process_returncode = process.wait()
    _join_output_readers(readers)
    _abandon_unfinished_readers(readers, evidence)
    return process_returncode


def _run_command(command: list[str], evidence: _CaptureEvidence) -> int:
    """Start the command, preserving actionable startup errors as stderr."""
    try:
        # Workflow callers pass a fixed argv vector; no shell text is evaluated.
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as error:
        diagnostic = f"Could not start mutation command: {error}\n".encode()
        evidence._record_chunk("stderr", diagnostic)
        evidence._forward_output("stderr", diagnostic)
        return 127
    return _capture_process_output(process, evidence)


def _finalize_capture(
    evidence: _CaptureEvidence, metadata_path: Path, process_returncode: int
) -> int:
    """Write completed metadata and return a shell-compatible command status."""
    wrapper_exit_code = (
        process_returncode if process_returncode >= 0 else 128 + abs(process_returncode)
    )
    if evidence._has_errors and wrapper_exit_code == 0:
        print(
            "ERROR: mutation command succeeded but its diagnostic capture failed: "
            + "; ".join(evidence.capture_errors),
            file=sys.stderr,
        )
        wrapper_exit_code = 1

    evidence._finalize()
    final_metadata = evidence._to_metadata(
        state="complete",
        exit_code=wrapper_exit_code,
        process_returncode=process_returncode,
    )
    try:
        _write_metadata(metadata_path, final_metadata)
    except OSError as error:
        print(
            f"ERROR: cannot finalize mutation diagnostic metadata: {error}",
            file=sys.stderr,
        )
        if wrapper_exit_code == 0:
            wrapper_exit_code = 1
    return wrapper_exit_code


def _capture_error(command: list[str], max_bytes_per_stream: int) -> str | None:
    """Return a validation error before opening any output files."""
    if not command:
        return "capture mode requires a command"
    if max_bytes_per_stream <= 0:
        return "the per-stream capture limit must be positive"
    return None


def capture_command(
    command: list[str],
    output_dir: Path,
    *,
    max_bytes_per_stream: int = DEFAULT_MAX_BYTES_PER_STREAM,
) -> int:
    """Capture command startup and execution output, preserving its failure.

    Parameters
    ----------
    command : list[str]
        Executable and arguments to run without a shell.
    output_dir : pathlib.Path
        Directory receiving ``stdout.log``, ``stderr.log`` and
        ``metadata.json``. The two logs retain their first bytes independently.
    max_bytes_per_stream : int, default 512 KiB
        Maximum number of bytes retained from each stream. Additional bytes
        are drained and counted, but discarded so a noisy failure cannot grow
        the uploaded diagnostic artefact without bound.

    Returns
    -------
    int
        The command's shell-compatible status. A capture failure changes a
        successful command to failure; it never changes a failed command to
        success or replaces its non-zero status.
    """
    validation_error = _capture_error(command, max_bytes_per_stream)
    if validation_error:
        print(f"ERROR: {validation_error}", file=sys.stderr)
        return 2

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as stream_stack:
            stream_files: dict[str, typ.BinaryIO] = {
                name: stream_stack.enter_context(
                    (output_dir / f"{name}.log").open("wb")
                )
                for name in ("stdout", "stderr")
            }
            evidence = _CaptureEvidence(max_bytes_per_stream, stream_files)
            metadata_path = output_dir / "metadata.json"
            try:
                _write_metadata(
                    metadata_path,
                    evidence._to_metadata(
                        state="running", exit_code=None, process_returncode=None
                    ),
                )
            except OSError as error:
                print(
                    f"ERROR: cannot initialize mutation diagnostic metadata: {error}",
                    file=sys.stderr,
                )
                return 1

            process_returncode = _run_command(command, evidence)
            try:
                stream_stack.pop_all().close()
            except OSError as error:
                evidence._record_error(f"could not close a diagnostic stream: {error}")
            return _finalize_capture(evidence, metadata_path, process_returncode)
    except OSError as error:
        print(f"ERROR: cannot create mutation diagnostics: {error}", file=sys.stderr)
        return 1


def verify_failure_capture(output_dir: Path) -> int:
    """Require complete diagnostic evidence after a failed mutation step.

    Parameters
    ----------
    output_dir : pathlib.Path
        Directory expected to contain both stream logs and completed metadata.

    Returns
    -------
    int
        Zero when the failed command has complete evidence, otherwise one
        after writing an actionable explanation to standard error.
    """
    problems, is_incomplete = _failure_capture_problems(output_dir)
    if not problems:
        return 0
    _report_capture_problems(output_dir, problems, is_incomplete)
    return 1


def _metadata_problems(metadata_path: Path) -> tuple[list[str], str | None]:
    """Return metadata failures and whether the capture stopped incomplete."""
    if not metadata_path.is_file():
        return [], None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"metadata.json cannot be read: {error}"], None
    if not isinstance(metadata, dict):
        return ["metadata.json does not contain a JSON object"], None

    problems: list[str] = []
    state = metadata.get("state")
    incomplete_state = str(state) if state != "complete" else None
    if incomplete_state is not None:
        problems.append(f"capture state={state}")
    if metadata.get("exit_code") in (None, 0):
        problems.append("the capture has no non-zero mutation exit status")
    capture_errors = metadata.get("capture_errors")
    if capture_errors:
        problems.append(f"capture errors: {capture_errors}")
    return problems, incomplete_state


def _failure_capture_problems(output_dir: Path) -> tuple[list[str], str | None]:
    """Collect missing stream files and invalid metadata for a failed step."""
    required = ("stdout.log", "stderr.log", "metadata.json")
    missing = [name for name in required if not (output_dir / name).is_file()]
    problems = [f"missing files: {', '.join(missing)}"] if missing else []
    metadata_problems, is_incomplete = _metadata_problems(output_dir / "metadata.json")
    return [*problems, *metadata_problems], is_incomplete


def _report_capture_problems(
    output_dir: Path, problems: list[str], incomplete_state: str | None
) -> None:
    """Print an actionable failure message for missing diagnostic evidence."""
    required_files = "stdout.log, stderr.log and completed metadata"
    explanation = (
        "The failed mutation step remains failed; inspect its step log "
        "and rerun if needed."
    )
    print(
        "ERROR: mutation failure diagnostics are missing or incomplete: "
        + "; ".join(problems)
        + f". Expected {required_files} in {output_dir}. {explanation}",
        file=sys.stderr,
    )
    if incomplete_state == "running":
        print(
            "The capture is incomplete after a possible timeout or cancellation; "
            "partial logs are best-effort evidence.",
            file=sys.stderr,
        )
    elif incomplete_state is not None:
        print(
            "The capture did not complete cleanly; a pipe did not reach EOF or "
            "capture failed. Partial logs are best-effort evidence.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    """Run capture mode or validate evidence after a failed mutation step.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments, excluding the script name. ``None`` reads
        arguments from ``sys.argv``.

    Returns
    -------
    int
        The wrapped command status, or the verification status in
        ``--verify-failure`` mode.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--max-bytes-per-stream", type=int, default=DEFAULT_MAX_BYTES_PER_STREAM
    )
    parser.add_argument("--verify-failure", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)

    if arguments.verify_failure:
        if arguments.command:
            parser.error("--verify-failure does not accept a command")
        return verify_failure_capture(arguments.output_dir)

    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("capture mode requires a command after --")
    return capture_command(
        command,
        arguments.output_dir,
        max_bytes_per_stream=arguments.max_bytes_per_stream,
    )


if __name__ == "__main__":
    sys.exit(main())
