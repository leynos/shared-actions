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

The empty run
-------------
cargo-mutants exits ``0`` both when every mutant was caught and when it
found no mutants at all, so a lane whose filters match nothing reports a
clean pass on an empty run. Four consumers were doing exactly that. This
script separates the two by reading the mutant inventory cargo-mutants
writes to ``mutants.out/mutants.json``: an empty inventory is reported as
``no-mutants`` and fails the step unless ``INPUT_ALLOW_NO_MUTANTS`` says
the caller expects it.

A sharded run is exempt. Sharding splits the inventory after enumeration,
so a shard with nothing to do is ordinary when the project has fewer
mutants than shards, and failing it would be a false alarm. The vacuous
passes all came from unsharded scoped runs, which is what this catches.

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
INPUT_RUSTFLAGS : str, optional
    Nonempty literal replacement; removes encoded Rustflags in the child only.
INPUT_ALLOW_NO_MUTANTS : str, optional
    ``true`` to accept a run that found no mutants. Default: ``false``.

Usage
-----
As a workflow step::

    - run: uv run --script workflow_scripts/mutation_run_cargo.py
      env:
        INPUT_FILES: ${{ matrix.files }}
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

from cyclopts import App, Parameter
from plumbum import RETCODE, local

if __package__:
    from .mutation_cargo_command import (
        MutantsInvocation,
        build_arguments,
        cargo_command,
    )
    from .output import emit, fail
else:
    from mutation_cargo_command import (
        MutantsInvocation,
        build_arguments,
        cargo_command,
    )
    from output import emit, fail  # type: ignore[import-not-found,no-redef]

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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


#: Where cargo-mutants records the mutants it enumerated for this run.
MUTANT_INVENTORY = pathlib.Path("mutants.out") / "mutants.json"

#: Reported when the run enumerated nothing. Distinct from "all mutants
#: caught", which is what an empty run used to be recorded as.
NO_MUTANTS = "no mutants found"


def resolve_output_dir(target_dir: str, arguments: cabc.Sequence[str]) -> str:
    """Find where cargo-mutants will write ``mutants.out``.

    It writes beside the crate by default, but ``--output`` moves it, and
    a caller can pass that through ``extra-args``. Reading the default
    location regardless would find nothing, report the inventory as
    unknown, and hand the empty run back its clean pass.

    Parameters
    ----------
    target_dir : str
        The crate directory the run mutated.
    arguments : cabc.Sequence[str]
        The full argument list handed to cargo.

    Returns
    -------
    str
        The directory that will contain ``mutants.out``. The last
        ``--output`` wins, as it does for cargo-mutants itself.
    """
    output: str | None = None
    pending = False
    for argument in arguments:
        if pending:
            output = argument
            pending = False
        elif argument == "--output":
            pending = True
        elif argument.startswith("--output="):
            output = argument.partition("=")[2]
    return output if output is not None else target_dir


def count_enumerated_mutants(output_dir: str) -> int | None:
    """Count the mutants cargo-mutants enumerated for this run.

    Parameters
    ----------
    output_dir : str
        The directory containing ``mutants.out``.

    Returns
    -------
    int | None
        The mutant count, or ``None`` when the inventory is missing or
        unreadable. ``None`` is deliberately not zero: a version that
        stops writing the file, or writes it somewhere else, must not
        start failing every caller's run.
    """
    inventory = pathlib.Path(output_dir) / MUTANT_INVENTORY
    try:
        listed = json.loads(inventory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _unreadable_inventory(inventory)
        return None
    match listed:
        case list():
            emit("mutation_cargo_inventory", "readable")
            return len(listed)
        case _:
            _unreadable_inventory(inventory)
            return None


def _unreadable_inventory(inventory: pathlib.Path) -> None:
    """Announce an inventory that could not be read."""
    # Unknown keeps the run passing, which is right, but it must not do
    # so quietly: a cargo-mutants that stopped writing this file would
    # otherwise restore the vacuous pass across every consumer at once,
    # with nothing in the log to say so.
    print(
        f"::warning title=Mutation testing::the mutant inventory at "
        f"{inventory} could not be read, so an empty run cannot be told "
        f"from a full one"
    )
    emit("mutation_cargo_inventory", "unreadable")


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


def classify_empty_run(
    *,
    meaning: str,
    enumerated: int | None,
    sharded: bool,
    allowed: bool,
) -> tuple[bool, str]:
    """Separate a clean pass from an empty one.

    cargo-mutants exits ``0`` for both, so the exit code alone cannot tell
    a lane that caught every mutant from a lane whose filters matched
    nothing.

    Parameters
    ----------
    meaning : str
        The meaning the exit code alone gives, used when the run was not
        empty or cannot be shown to be.
    enumerated : int | None
        Mutants enumerated, or ``None`` when the inventory is unreadable.
    sharded : bool
        Whether the run was one shard of several.
    allowed : bool
        Whether the caller opted in to an empty run.

    Returns
    -------
    tuple[bool, str]
        ``(is_success, human_readable_meaning)``.
    """
    if enumerated is None or enumerated > 0:
        return True, meaning
    if sharded:
        # Sharding splits the inventory after enumeration, so an empty
        # shard is ordinary when there are fewer mutants than shards.
        return True, f"{NO_MUTANTS} in this shard"
    if allowed:
        print(
            "::notice title=Mutation testing::no mutants were found, "
            "which this caller has declared expected"
        )
        return True, f"{NO_MUTANTS} (allowed)"
    print(
        "::error title=Mutation testing::no mutants were found, so this run "
        "proved nothing; widen the filters, or set allow-no-mutants: true if "
        "an empty run is expected here"
    )
    return False, NO_MUTANTS


def report_outcome(
    *,
    code: int,
    output_dir: str,
    sharded: bool,
    allow_no_mutants: str,
) -> None:
    """Emit the run's outcome and fail the step when it is a fault.

    Parameters
    ----------
    code : int
        The exit code cargo-mutants returned.
    output_dir : str
        The directory containing ``mutants.out``.
    sharded : bool
        Whether the run was one shard of several.
    allow_no_mutants : str
        The caller's opt-out, as the environment spells it.

    Raises
    ------
    SystemExit
        With the tool's code on a genuine fault, or 1 when an unsharded
        run enumerated no mutants and the caller did not opt in.
    """
    success, meaning = interpret_exit_code(code)
    emit("mutation_cargo_exit_code", code)
    if code == 0:
        success, meaning = classify_empty_run(
            meaning=meaning,
            enumerated=count_enumerated_mutants(output_dir),
            sharded=sharded,
            allowed=allow_no_mutants.strip().lower() == "true",
        )
    emit("mutation_cargo_outcome", meaning)
    if not success:
        raise SystemExit(code or 1)


def _run_invocation(
    invocation: MutantsInvocation, *, rustflags: str, allow_no_mutants: str
) -> None:
    """Validate and execute one mutation target, then report its tool outcome."""
    if invocation.shard_count < 1:
        fail(f"shard-count must be at least 1, got {invocation.shard_count}")
    if not 0 <= invocation.shard < invocation.shard_count:
        fail(f"shard must be in [0, {invocation.shard_count}), got {invocation.shard}")

    arguments = build_arguments(invocation)
    emit("mutation_cargo_command", ["cargo", *arguments])
    try:
        command = cargo_command(local, arguments, rustflags)
    except ValueError as error:
        emit("mutation_cargo_rustflags_mode", "invalid")
        fail(str(error))
    emit("mutation_cargo_rustflags_mode", "override" if rustflags else "inherited")
    code = command & RETCODE(FG=True)
    report_outcome(
        code=code,
        output_dir=resolve_output_dir(invocation.target_dir, arguments),
        sharded=invocation.shard_count > 1,
        allow_no_mutants=allow_no_mutants,
    )


#: Frozen scalar target defaults are safe to share across CLI invocations.
_DEFAULT_INVOCATION: typ.Final = MutantsInvocation()


@app.default
def main(
    *,
    invocation: typ.Annotated[
        MutantsInvocation, Parameter(name="*")
    ] = _DEFAULT_INVOCATION,
    rustflags: typ.Annotated[str, Parameter(env_var="INPUT_RUSTFLAGS")] = "",
    allow_no_mutants: typ.Annotated[
        str, Parameter(env_var="INPUT_ALLOW_NO_MUTANTS")
    ] = "false",
) -> None:
    """Run cargo-mutants for one matrix target.

    Python callers can use ``main(invocation=MutantsInvocation(files="src/lib.rs"))``
    to mutate that file; command-line options remain flat.

    Parameters
    ----------
    invocation : MutantsInvocation
        Mutation target; Cyclopts exposes its fields as the existing flat options.
    rustflags : str
        Nonempty literal replacement for the Cargo child; empty inherits flags.
    allow_no_mutants : str
        ``true`` to accept a run that enumerated no mutants.

    Raises
    ------
    SystemExit
        Exits with the tool's code when it signals a genuine fault
        (anything outside ``{0, 2, 3}``), with 1 on invalid inputs, and
        with 1 when an unsharded run enumerated no mutants and the caller
        did not opt in to that.
    """
    _run_invocation(
        invocation,
        rustflags=rustflags,
        allow_no_mutants=allow_no_mutants,
    )


if __name__ == "__main__":
    app()
