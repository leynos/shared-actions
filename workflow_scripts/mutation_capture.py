#!/usr/bin/env python3
"""Capture bounded stdout and stderr around a mutation workflow command."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import typing as typ
from pathlib import Path

DEFAULT_MAX_BYTES_PER_STREAM = 512 * 1024


def _write_metadata(path: Path, metadata: dict[str, object]) -> None:
    """Atomically replace capture metadata so readers never see partial JSON."""
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def _metadata(
    *,
    state: str,
    exit_code: int | None,
    process_returncode: int | None,
    max_bytes_per_stream: int,
    counts: dict[str, dict[str, int]],
    capture_errors: list[str],
) -> dict[str, object]:
    """Build the versioned evidence envelope for the two captured streams."""
    return {
        "capture_errors": capture_errors,
        "exit_code": exit_code,
        "max_bytes_per_stream": max_bytes_per_stream,
        "process_returncode": process_returncode,
        "retention": (
            "The first bytes from each stream are kept; later bytes are discarded."
        ),
        "state": state,
        "stderr": counts["stderr"],
        "stdout": counts["stdout"],
    }


def _forward_output(stream_name: str, chunk: bytes, errors: list[str]) -> None:
    """Keep the original command output visible in the Actions step log."""
    target = sys.stdout.buffer if stream_name == "stdout" else sys.stderr.buffer
    try:
        target.write(chunk)
        target.flush()
    except (BrokenPipeError, OSError) as error:
        message = f"could not forward {stream_name} to the workflow log: {error}"
        if message not in errors:
            errors.append(message)


def _record_chunk(
    stream_name: str,
    chunk: bytes,
    stream_files: dict[str, typ.BinaryIO],
    counts: dict[str, dict[str, int]],
    errors: list[str],
    limit: int,
) -> None:
    """Retain only each stream's prefix while counting all observed bytes."""
    stream_count = counts[stream_name]
    stream_count["bytes_seen"] += len(chunk)
    remaining = limit - stream_count["bytes_saved"]
    retained = chunk[:remaining] if remaining > 0 else b""
    if retained:
        try:
            stream_files[stream_name].write(retained)
            stream_files[stream_name].flush()
            stream_count["bytes_saved"] += len(retained)
        except OSError as error:
            message = f"could not save {stream_name} diagnostics: {error}"
            if message not in errors:
                errors.append(message)
    stream_count["truncated_bytes"] = (
        stream_count["bytes_seen"] - stream_count["bytes_saved"]
    )


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
    if not command:
        print("ERROR: capture mode requires a command", file=sys.stderr)
        return 2
    if max_bytes_per_stream <= 0:
        print("ERROR: the per-stream capture limit must be positive", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {
        name: {"bytes_seen": 0, "bytes_saved": 0, "truncated_bytes": 0}
        for name in ("stdout", "stderr")
    }
    capture_errors: list[str] = []
    metadata_path = output_dir / "metadata.json"
    initial_metadata = _metadata(
        state="running",
        exit_code=None,
        process_returncode=None,
        max_bytes_per_stream=max_bytes_per_stream,
        counts=counts,
        capture_errors=capture_errors,
    )

    try:
        stream_files: dict[str, typ.BinaryIO] = {
            "stdout": (output_dir / "stdout.log").open("wb", buffering=0),
            "stderr": (output_dir / "stderr.log").open("wb", buffering=0),
        }
    except OSError as error:
        print(f"ERROR: cannot create mutation diagnostics: {error}", file=sys.stderr)
        return 1

    try:
        _write_metadata(metadata_path, initial_metadata)
    except OSError as error:
        print(
            f"ERROR: cannot initialize mutation diagnostic metadata: {error}",
            file=sys.stderr,
        )
        for stream_file in stream_files.values():
            stream_file.close()
        return 1

    process_returncode: int
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
        _record_chunk(
            "stderr",
            diagnostic,
            stream_files,
            counts,
            capture_errors,
            max_bytes_per_stream,
        )
        _forward_output("stderr", diagnostic, capture_errors)
        process_returncode = 127
    else:
        if process.stdout is None or process.stderr is None:
            capture_errors.append(
                "the mutation process did not provide both output pipes"
            )
            process.kill()
            process_returncode = process.wait()
        else:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    for key, _ in selector.select():
                        chunk = os.read(key.fileobj.fileno(), 16 * 1024)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        stream_name = key.data
                        _record_chunk(
                            stream_name,
                            chunk,
                            stream_files,
                            counts,
                            capture_errors,
                            max_bytes_per_stream,
                        )
                        _forward_output(stream_name, chunk, capture_errors)
            process_returncode = process.wait()

    for stream_file in stream_files.values():
        try:
            stream_file.close()
        except OSError as error:
            capture_errors.append(f"could not close a diagnostic stream: {error}")

    shell_exit_code = (
        process_returncode if process_returncode >= 0 else 128 + abs(process_returncode)
    )
    wrapper_exit_code = shell_exit_code
    if capture_errors and wrapper_exit_code == 0:
        print(
            "ERROR: mutation command succeeded but its diagnostic capture failed: "
            + "; ".join(capture_errors),
            file=sys.stderr,
        )
        wrapper_exit_code = 1

    final_metadata = _metadata(
        state="complete",
        exit_code=wrapper_exit_code,
        process_returncode=process_returncode,
        max_bytes_per_stream=max_bytes_per_stream,
        counts=counts,
        capture_errors=capture_errors,
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
    required = [
        output_dir / "stdout.log",
        output_dir / "stderr.log",
        output_dir / "metadata.json",
    ]
    missing = [path.name for path in required if not path.is_file()]
    problems: list[str] = []
    if missing:
        problems.append(f"missing files: {', '.join(missing)}")

    metadata: object = {}
    metadata_path = output_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"metadata.json cannot be read: {error}")

    if isinstance(metadata, dict):
        state = metadata.get("state")
        if state != "complete":
            problems.append(f"capture state={state}")
        if metadata.get("exit_code") in (None, 0):
            problems.append("the capture has no non-zero mutation exit status")
        capture_errors = metadata.get("capture_errors")
        if capture_errors:
            problems.append(f"capture errors: {capture_errors}")
    elif not missing:
        problems.append("metadata.json does not contain a JSON object")

    if problems:
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
        if any(problem.startswith("capture state=") for problem in problems):
            print(
                "The capture is incomplete after a possible timeout or cancellation; "
                "partial logs are best-effort evidence.",
                file=sys.stderr,
            )
        return 1
    return 0


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
