"""Shared helpers for normalising plumbum command results in tests."""

from __future__ import annotations

from plumbum.commands.processes import ProcessExecutionError

import cmd_utils


def run_plumbum_command(
    command: cmd_utils.SupportsFormulate,
    *,
    method: cmd_utils.RunMethod = "run",
    **run_kwargs: object,
) -> cmd_utils.RunResult:
    """Execute *command* and always return a :class:`RunResult`."""
    try:
        outcome = cmd_utils.run_cmd(command, method=method, **run_kwargs)
    except ProcessExecutionError as exc:
        return cmd_utils.process_error_to_run_result(exc)
    return cmd_utils.coerce_run_result(outcome)


__all__ = ["run_plumbum_command"]
