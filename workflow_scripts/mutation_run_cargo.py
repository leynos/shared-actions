#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "plumbum>=1.8,<3.0"]
# ///

"""Run cargo-mutants with the informational exit-code contract.

Invokes ``cargo mutants`` for one target of the mutation-testing matrix,
scoped to the given files (or unscoped for full runs, optionally as one
shard of many), and maps the tool's exit code onto the workflow contract:
missed mutants and timeouts are informative outcomes, not failures.

Exit-code contract (from the cargo-mutants handbook)
----------------------------------------------------
``0``
    Success; every viable mutant was caught.
``2`` / ``3``
    Missed mutants / timeouts — the workflow's deliverable, treated as
    success here.
``1`` / ``4`` / ``70``
    Usage error / failing baseline / internal error — genuine faults that
    fail the step, as does any other unexpected code.

Environment Variables
---------------------
INPUT_DIR : str, optional
    Crate directory to mutate (``--dir``). Default: ``.`` (root).
INPUT_FILES : str, optional
    Space-separated file paths relative to ``INPUT_DIR``; each becomes a
    ``--file`` argument. Empty means a full run.
INPUT_SHARD : int, optional
    Zero-based shard index. Default: ``0``.
INPUT_SHARD_COUNT : int, optional
    Total shard count; values above 1 add ``--shard k/N``. Default: ``1``.
INPUT_TIMEOUT_MULTIPLIER : str, optional
    Per-mutant timeout multiplier. Default: ``3``.
INPUT_EXCLUDE_GLOBS : str, optional
    Comma-separated globs passed as repeated ``--exclude`` arguments.
INPUT_EXTRA_ARGS : str, optional
    Extra arguments appended verbatim (shell-lexed), e.g.
    ``--all-features`` so feature-gated tests run.

Usage
-----
As a workflow step::

    - run: uv run --script workflow_scripts/mutation_run_cargo.py
      env:
        INPUT_FILES: ${{ matrix.files }}
"""

from __future__ import annotations

import shlex
import typing as typ

from cyclopts import App, Parameter
from plumbum import RETCODE, local

if __package__:
    from .output import emit, fail
else:
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

app = App()

#: cargo-mutants exit codes that are informative outcomes, not failures.
INFORMATIVE_EXIT_CODES: frozenset[int] = frozenset({0, 2, 3})

EXIT_CODE_MEANINGS = {
    0: "all mutants caught",
    1: "usage error",
    2: "missed mutants",
    3: "test timeouts",
    4: "baseline tests failing",
    70: "internal error",
}


def build_arguments(
    *,
    files: str = "",
    shard: int = 0,
    shard_count: int = 1,
    timeout_multiplier: str = "3",
    exclude_globs: str = "",
    extra_args: str = "",
    target_dir: str = ".",
) -> list[str]:
    """Build the ``cargo mutants`` argument list for one matrix target.

    Parameters
    ----------
    files : str
        Space-separated file paths relative to ``target_dir``.
    shard : int
        Zero-based shard index.
    shard_count : int
        Total shard count; only values above 1 emit ``--shard``.
    timeout_multiplier : str
        Per-mutant timeout multiplier.
    exclude_globs : str
        Comma-separated ``--exclude`` globs.
    extra_args : str
        Extra arguments appended verbatim (shell-lexed).
    target_dir : str
        Crate directory; values other than ``.`` emit ``--dir``.

    Returns
    -------
    list[str]
        Arguments for ``cargo``, starting with ``mutants``.
    """
    arguments = [
        "mutants",
        "--in-place",
        "--timeout-multiplier",
        timeout_multiplier,
    ]
    if target_dir != ".":
        arguments.extend(["--dir", target_dir])
    if shard_count > 1:
        arguments.extend(["--shard", f"{shard}/{shard_count}"])
    for name in files.split():
        arguments.extend(["--file", name])
    for glob in (g.strip() for g in exclude_globs.split(",")):
        if glob:
            arguments.extend(["--exclude", glob])
    arguments.extend(shlex.split(extra_args))
    return arguments


def interpret_exit_code(code: int) -> tuple[bool, str]:
    """Classify a cargo-mutants exit code under the workflow contract.

    Parameters
    ----------
    code : int
        The tool's exit code.

    Returns
    -------
    tuple[bool, str]
        ``(is_success, human_readable_meaning)``.
    """
    meaning = EXIT_CODE_MEANINGS.get(code, "unexpected exit code")
    return code in INFORMATIVE_EXIT_CODES, meaning


@app.default
def main(
    *,
    target_dir: typ.Annotated[str, Parameter(env_var="INPUT_DIR")] = ".",
    files: typ.Annotated[str, Parameter(env_var="INPUT_FILES")] = "",
    shard: typ.Annotated[int, Parameter(env_var="INPUT_SHARD")] = 0,
    shard_count: typ.Annotated[int, Parameter(env_var="INPUT_SHARD_COUNT")] = 1,
    timeout_multiplier: typ.Annotated[
        str, Parameter(env_var="INPUT_TIMEOUT_MULTIPLIER")
    ] = "3",
    exclude_globs: typ.Annotated[str, Parameter(env_var="INPUT_EXCLUDE_GLOBS")] = "",
    extra_args: typ.Annotated[str, Parameter(env_var="INPUT_EXTRA_ARGS")] = "",
) -> None:
    """Run cargo-mutants for one matrix target.

    Parameters
    ----------
    target_dir : str
        Crate directory to mutate.
    files : str
        Space-separated scoped files relative to ``target_dir``.
    shard : int
        Zero-based shard index.
    shard_count : int
        Total shard count.
    timeout_multiplier : str
        Per-mutant timeout multiplier.
    exclude_globs : str
        Comma-separated ``--exclude`` globs.
    extra_args : str
        Extra arguments appended verbatim.

    Raises
    ------
    SystemExit
        Exits with the tool's code when it signals a genuine fault
        (anything outside ``{0, 2, 3}``), and with 1 on invalid inputs.
    """
    if shard_count < 1:
        fail(f"shard-count must be at least 1, got {shard_count}")
    if not 0 <= shard < shard_count:
        fail(f"shard must be in [0, {shard_count}), got {shard}")

    arguments = build_arguments(
        files=files,
        shard=shard,
        shard_count=shard_count,
        timeout_multiplier=timeout_multiplier,
        exclude_globs=exclude_globs,
        extra_args=extra_args,
        target_dir=target_dir,
    )
    emit("mutation_cargo_command", ["cargo", *arguments])
    code = local["cargo"][arguments] & RETCODE(FG=True)
    success, meaning = interpret_exit_code(code)
    emit("mutation_cargo_exit_code", code)
    emit("mutation_cargo_outcome", meaning)
    if not success:
        raise SystemExit(code)


if __name__ == "__main__":
    app()
