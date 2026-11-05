"""Construct cargo-mutants arguments and its isolated child environment."""

from __future__ import annotations

import dataclasses
import shlex
import typing as typ

from cyclopts import Parameter

if typ.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand
    from plumbum.machines.local import LocalMachine


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

    files: typ.Annotated[str, Parameter(env_var="INPUT_FILES")] = ""
    shard: typ.Annotated[int, Parameter(env_var="INPUT_SHARD")] = 0
    shard_count: typ.Annotated[int, Parameter(env_var="INPUT_SHARD_COUNT")] = 1
    timeout_multiplier: typ.Annotated[
        str, Parameter(env_var="INPUT_TIMEOUT_MULTIPLIER")
    ] = "3"
    exclude_globs: typ.Annotated[str, Parameter(env_var="INPUT_EXCLUDE_GLOBS")] = ""
    extra_args: typ.Annotated[str, Parameter(env_var="INPUT_EXTRA_ARGS")] = ""
    target_dir: typ.Annotated[str, Parameter(env_var="INPUT_DIR")] = "."


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


def cargo_command(
    provider: LocalMachine, arguments: list[str], rustflags: str
) -> BaseCommand:
    """Build a Cargo command with an optional child-only flag replacement.

    Parameters
    ----------
    provider : LocalMachine
        The caller's command provider, retaining its injected tool resolution.
    arguments : list[str]
        Cargo arguments, including the ``mutants`` subcommand.
    rustflags : str
        Literal replacement flags; empty preserves the inherited environment.

    Returns
    -------
    BaseCommand
        Cargo directly, or ``env -u`` executing the same resolved Cargo binary.

    Raises
    ------
    ValueError
        If the replacement contains a newline, carriage return or NUL.

    Examples
    --------
    ``cargo_command(local, ["mutants"], "-D warnings")`` replaces Rustflags
    only for that child and removes its inherited encoded Rustflags.
    """
    if any(character in rustflags for character in ("\r", "\n", "\0")):
        message = "rustflags must be a single line without CR, LF or NUL"
        raise ValueError(message)
    cargo = provider["cargo"]
    if not rustflags:
        return cargo[arguments]
    # Plumbum merges environment mappings; only env -u removes an inherited key.
    return provider["env"][
        "-u",
        "CARGO_ENCODED_RUSTFLAGS",
        "RUSTFLAGS=" + rustflags,
        cargo.executable,
        *arguments,
    ]
