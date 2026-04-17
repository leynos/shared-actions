from __future__ import annotations

import contextlib
import dataclasses
import os
import selectors
import shlex
import subprocess
import sys
import threading
import time
import traceback
import typing as typ

import typer
from plumbum.cmd import cargo


@dataclasses.dataclass
class _CargoProcCtx:
    """Cargo process handle together with its timing constraints."""

    proc: subprocess.Popen[str]
    deadline: float
    wait_timeout: float


def _safe_close_text_stream(stream: typ.TextIO | None) -> None:
    """Close ``stream`` while suppressing any cleanup errors."""
    if stream is None:
        return
    with contextlib.suppress(Exception):
        stream.close()


def _is_debug_pump_enabled() -> bool:
    """Return ``True`` if cargo pump-thread debug logging is requested."""
    return os.environ.get("RUN_RUST_DEBUG") == "1" or bool(os.environ.get("DEBUG_UTF8"))


def _pump_stream_thread(
    src: typ.IO[str],
    *,
    to_stdout: bool,
    stdout_lines: list[str],
    thread_exceptions: list[Exception],
) -> None:
    """Read lines from *src*, echo them, and capture stdout lines.

    Thread exceptions are appended to *thread_exceptions* rather than
    propagated so that the monitoring loop can handle them.
    """
    dest = sys.stdout if to_stdout else sys.stderr
    try:
        for line in iter(src.readline, ""):
            dest.write(line)
            dest.flush()
            if to_stdout:
                stdout_lines.append(line.rstrip("\r\n"))
    except Exception as exc:  # noqa: BLE001
        thread_exceptions.append(exc)
        if _is_debug_pump_enabled():
            sys.stderr.write(f"Exception in pump thread: {exc}\n")
            sys.stderr.write(traceback.format_exc())


def _poll_pump_loop_iteration(
    threads: list[threading.Thread],
    ctx: _CargoProcCtx,
    thread_exceptions: list[Exception],
) -> bool:
    """Perform one monitoring iteration; return ``True`` when pumping is done."""
    if thread_exceptions:
        with contextlib.suppress(Exception):
            ctx.proc.kill()
        return True
    if not any(t.is_alive() for t in threads):
        return True
    remaining = max(0.0, ctx.deadline - time.time())
    if remaining <= 0:
        _raise_cargo_timeout(ctx.proc, wait_timeout=ctx.wait_timeout)
    join_timeout = min(0.1, remaining)
    for thread in threads:
        thread.join(timeout=join_timeout)
    return False


def _finalise_pump_threads(
    threads: list[threading.Thread],
    proc: subprocess.Popen[str],
    thread_exceptions: list[Exception],
) -> None:
    """Join threads, kill on timeout, and re-raise any captured thread exception."""
    timed_out = False
    for thread in threads:
        thread.join(timeout=5)
        if thread.is_alive():
            timed_out = True
    if timed_out:
        with contextlib.suppress(Exception):
            proc.kill()
        thread_exceptions.append(
            TimeoutError("cargo output pump threads did not terminate in time")
        )
    if thread_exceptions:
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        raise thread_exceptions[0]


def _pump_cargo_output_windows(
    stdout_stream: typ.IO[str],
    stderr_stream: typ.IO[str],
    ctx: _CargoProcCtx,
) -> list[str]:
    """Pump cargo output on Windows using background threads."""
    thread_exceptions: list[Exception] = []
    stdout_lines: list[str] = []

    threads = [
        threading.Thread(
            name="cargo-stdout",
            target=_pump_stream_thread,
            args=(stdout_stream,),
            kwargs={
                "to_stdout": True,
                "stdout_lines": stdout_lines,
                "thread_exceptions": thread_exceptions,
            },
        ),
        threading.Thread(
            name="cargo-stderr",
            target=_pump_stream_thread,
            args=(stderr_stream,),
            kwargs={
                "to_stdout": False,
                "stdout_lines": stdout_lines,
                "thread_exceptions": thread_exceptions,
            },
        ),
    ]
    for thread in threads:
        thread.start()
    while not _poll_pump_loop_iteration(threads, ctx, thread_exceptions):
        pass
    _finalise_pump_threads(threads, ctx.proc, thread_exceptions)
    return stdout_lines


def _handle_cargo_output_event(
    key: selectors.SelectorKey,
    stdout_lines: list[str],
    sel: selectors.DefaultSelector,
) -> None:
    """Process one selector event from a cargo output stream.

    Unregisters the stream when EOF is reached; otherwise echoes the line
    and, for stdout, appends it to *stdout_lines*.
    """
    stream = typ.cast("typ.TextIO", key.fileobj)
    line = stream.readline()
    if not line:
        sel.unregister(stream)
        return
    if key.data == "stdout":
        typer.echo(line, nl=False)
        stdout_lines.append(line.rstrip("\r\n"))
    else:
        typer.echo(line, err=True, nl=False)


def _kill_cargo_process(proc: subprocess.Popen[str]) -> None:
    """Kill *proc* and wait for termination, suppressing any errors."""
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def _pump_cargo_output(
    proc: subprocess.Popen[str],
    *,
    deadline: float,
    wait_timeout: float,
) -> list[str]:
    """Pump ``proc`` output streams to console and collect stdout lines."""
    if proc.stdout is None or proc.stderr is None:  # pragma: no cover - defensive
        message = (
            "cargo output streams must be captured.\n"
            f"proc.stdout: {proc.stdout}\n"
            f"proc.stderr: {proc.stderr}\n"
            f"proc.args: {getattr(proc, 'args', None)}"
        )
        raise RuntimeError(message)

    stdout_stream = proc.stdout
    stderr_stream = proc.stderr
    stdout_lines: list[str] = []
    ctx = _CargoProcCtx(proc=proc, deadline=deadline, wait_timeout=wait_timeout)

    if os.name == "nt":
        return _pump_cargo_output_windows(stdout_stream, stderr_stream, ctx)

    sel = selectors.DefaultSelector()
    try:
        sel.register(stdout_stream, selectors.EVENT_READ, data="stdout")
        sel.register(stderr_stream, selectors.EVENT_READ, data="stderr")

        while sel.get_map():
            if time.time() >= ctx.deadline:
                _raise_cargo_timeout(ctx.proc, wait_timeout=ctx.wait_timeout)

            timeout = max(0.0, ctx.deadline - time.time())
            for key, _ in sel.select(timeout):
                _handle_cargo_output_event(key, stdout_lines, sel)
    except Exception:
        _kill_cargo_process(ctx.proc)
        raise
    finally:
        sel.close()

    return stdout_lines


def _build_cargo_env(
    env_overrides: typ.Mapping[str, str] | None,
    env_unsets: typ.Iterable[str],
) -> dict[str, str]:
    """Return the environment dict for a spawned cargo process.

    Starts from a copy of ``os.environ``, removes every key in
    ``env_unsets``, then merges ``env_overrides`` (if provided).
    """
    env = dict(os.environ)
    for key in env_unsets:
        env.pop(key, None)
    if env_overrides is not None:
        env.update(env_overrides)
    return env


def _assert_cargo_streams(proc: subprocess.Popen[str]) -> None:
    """Raise ``typer.Exit(1)`` if stdout or stderr were not captured.

    Kills and cleans up the process before raising so no resources leak.
    """
    if proc.stdout is not None and proc.stderr is not None:
        return
    missing_streams = []
    if proc.stdout is None:
        missing_streams.append("stdout")
    if proc.stderr is None:
        missing_streams.append("stderr")
    missing = ", ".join(missing_streams)
    message = f"cargo output streams not captured: missing {missing}"
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)
    _safe_close_text_stream(typ.cast("typ.TextIO | None", proc.stdout))
    _safe_close_text_stream(typ.cast("typ.TextIO | None", proc.stderr))
    typer.echo(f"::error::{message}", err=True)
    raise typer.Exit(1) from None


def _raise_cargo_timeout(
    proc: subprocess.Popen[str], *, wait_timeout: float
) -> typ.Never:
    """Kill ``proc`` and raise ``typer.Exit(1)`` for a cargo timeout."""
    typer.echo(
        f"::error::cargo did not exit within {wait_timeout}s; killing",
        err=True,
    )
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)
    raise typer.Exit(1) from None


def _wait_for_cargo(
    proc: subprocess.Popen[str], *, deadline: float, wait_timeout: float
) -> int:
    """Wait for cargo to exit and return its return code.

    Kills the process and raises ``typer.Exit(1)`` if it does not exit
    within ``RUN_RUST_CARGO_WAIT_TIMEOUT`` seconds (default 600).
    """
    try:
        return proc.wait(timeout=max(0.0, deadline - time.time()))
    except subprocess.TimeoutExpired:
        _raise_cargo_timeout(proc, wait_timeout=wait_timeout)


def _spawn_cargo(
    command: typ.Any,  # noqa: ANN401
    env: dict[str, str],
) -> subprocess.Popen[str]:
    """Spawn a ``cargo`` subprocess with the given environment.

    Handles both direct ``popen`` invocation and plumbum machine-env
    contexts transparently.
    """
    popen_kwargs: dict[str, typ.Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    machine_env = getattr(getattr(cargo, "machine", None), "env", None)
    if machine_env is None:
        return command.popen(**popen_kwargs, env=env)
    with machine_env():
        machine_env.clear()
        machine_env.update(env)
        return command.popen(**popen_kwargs)


def _run_cargo(
    args: list[str],
    *,
    env_overrides: typ.Mapping[str, str] | None = None,
    env_unsets: typ.Iterable[str] = (),
) -> str:
    """Run ``cargo`` with ``args`` streaming output and return ``stdout``.

    Builds a subprocess environment by copying ``os.environ``, removing every
    key in ``env_unsets``, and — when ``env_overrides`` is not ``None`` —
    merging its entries into that copy (overrides take precedence). The
    resulting environment is passed to the spawned ``cargo`` process, whose
    stdout and stderr are streamed to the current process.

    Parameters
    ----------
    args : list[str]
        Arguments forwarded verbatim to ``cargo``.
    env_overrides : Mapping[str, str] | None, optional
        Extra or replacement environment variables. When ``None`` (the
        default), the environment is inherited unchanged except for any
        ``env_unsets`` removals.
    env_unsets : Iterable[str], optional
        Variable names to remove from the inherited environment before
        ``env_overrides`` are applied. Missing keys are silently ignored.
        Unsets are performed before overrides, so ``env_overrides`` can
        unconditionally set a variable that may or may not have been
        inherited.

    Returns
    -------
    str
        Captured stdout from the ``cargo`` invocation.
    """
    typer.echo(f"$ cargo {shlex.join(args)}")
    env = _build_cargo_env(env_overrides, env_unsets)
    try:
        wait_timeout = float(os.getenv("RUN_RUST_CARGO_WAIT_TIMEOUT", "600"))
    except ValueError as exc:
        typer.echo(
            "::error::RUN_RUST_CARGO_WAIT_TIMEOUT must be a number",
            err=True,
        )
        raise typer.Exit(1) from exc
    deadline = time.time() + wait_timeout
    proc = _spawn_cargo(cargo[args], env)
    try:
        _assert_cargo_streams(proc)
        stdout_lines = _pump_cargo_output(
            proc,
            deadline=deadline,
            wait_timeout=wait_timeout,
        )
        retcode = _wait_for_cargo(
            proc,
            deadline=deadline,
            wait_timeout=wait_timeout,
        )
        if retcode != 0:
            typer.echo(
                f"cargo {shlex.join(args)} failed with code {retcode}",
                err=True,
            )
            raise typer.Exit(code=retcode or 1)
        return "\n".join(stdout_lines)
    finally:
        _safe_close_text_stream(proc.stdout)
        _safe_close_text_stream(proc.stderr)
