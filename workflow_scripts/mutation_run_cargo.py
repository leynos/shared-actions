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

import dataclasses
import json
import pathlib
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


@dataclasses.dataclass(frozen=True, slots=True)
class MutantsInvocation:
    """Configuration for one ``cargo mutants`` matrix target.

    Attributes
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
    """

    files: str = ""
    shard: int = 0
    shard_count: int = 1
    timeout_multiplier: str = "3"
    exclude_globs: str = ""
    extra_args: str = ""
    target_dir: str = "."


def build_arguments(invocation: MutantsInvocation) -> list[str]:
    """Build the ``cargo mutants`` argument list for one matrix target.

    Parameters
    ----------
    invocation : MutantsInvocation
        Target configuration (scoped files, sharding, excludes).

    Returns
    -------
    list[str]
        Arguments for ``cargo``, starting with ``mutants``.
    """
    arguments = [
        "mutants",
        "--in-place",
        "--timeout-multiplier",
        invocation.timeout_multiplier,
    ]
    if invocation.target_dir != ".":
        arguments.extend(["--dir", invocation.target_dir])
    if invocation.shard_count > 1:
        arguments.extend(["--shard", f"{invocation.shard}/{invocation.shard_count}"])
    for name in invocation.files.split():
        arguments.extend(["--file", name])
    for glob in (g.strip() for g in invocation.exclude_globs.split(",")):
        if glob:
            arguments.extend(["--exclude", glob])
    arguments.extend(shlex.split(invocation.extra_args))
    return arguments


#: Where cargo-mutants records the mutants it enumerated for this run.
MUTANT_INVENTORY = pathlib.Path("mutants.out") / "mutants.json"

#: Reported when the run enumerated nothing. Distinct from "all mutants
#: caught", which is what an empty run used to be recorded as.
NO_MUTANTS = "no mutants found"


def count_enumerated_mutants(target_dir: str) -> int | None:
    """Count the mutants cargo-mutants enumerated for this run.

    Parameters
    ----------
    target_dir : str
        The crate directory the run mutated.

    Returns
    -------
    int | None
        The mutant count, or ``None`` when the inventory is missing or
        unreadable. ``None`` is deliberately not zero: a version that
        stops writing the file, or writes it somewhere else, must not
        start failing every caller's run.
    """
    inventory = pathlib.Path(target_dir) / MUTANT_INVENTORY
    try:
        listed = json.loads(inventory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(listed, list):
        return None
    return len(listed)


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
    allow_no_mutants: typ.Annotated[
        str, Parameter(env_var="INPUT_ALLOW_NO_MUTANTS")
    ] = "false",
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
    if shard_count < 1:
        fail(f"shard-count must be at least 1, got {shard_count}")
    if not 0 <= shard < shard_count:
        fail(f"shard must be in [0, {shard_count}), got {shard}")

    arguments = build_arguments(
        MutantsInvocation(
            files=files,
            shard=shard,
            shard_count=shard_count,
            timeout_multiplier=timeout_multiplier,
            exclude_globs=exclude_globs,
            extra_args=extra_args,
            target_dir=target_dir,
        )
    )
    emit("mutation_cargo_command", ["cargo", *arguments])
    code = local["cargo"][arguments] & RETCODE(FG=True)
    success, meaning = interpret_exit_code(code)
    emit("mutation_cargo_exit_code", code)
    if code == 0:
        success, meaning = classify_empty_run(
            meaning=meaning,
            enumerated=count_enumerated_mutants(target_dir),
            sharded=shard_count > 1,
            allowed=allow_no_mutants.strip().lower() == "true",
        )
    emit("mutation_cargo_outcome", meaning)
    if not success:
        raise SystemExit(code or 1)


if __name__ == "__main__":
    app()
