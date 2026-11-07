"""Pure helpers for the ``build-only`` mode of the Rust coverage step.

``build-only`` compiles the instrumented test binaries without running them,
so that a compiler cache holds the entries a later ``cargo llvm-cov nextest``
run reads. ``cargo llvm-cov``'s own ``--no-run`` means "report without
running", so the build uses the underlying runner's flag under the environment
``cargo llvm-cov show-env`` prints. Everything here is a function of its
arguments; ``run_rust`` owns the subprocesses.
"""

from __future__ import annotations

import dataclasses
import shlex
import typing as typ
from pathlib import Path

#: Variables ``cargo llvm-cov show-env`` prints that must not reach a build
#: meant to share compiler-cache entries with ``cargo llvm-cov nextest``. The
#: real run leaves them out of the compiler's environment, and sccache hashes
#: the ``CARGO_*`` variables it sees, so one stray variable turns every hit
#: into a miss. The target directory travels as ``--target-dir`` instead.
SHOW_ENV_LEAKS = frozenset(
    {
        "CARGO_LLVM_COV_SHOW_ENV",
        "CARGO_LLVM_COV_TARGET_DIR",
        "CARGO_LLVM_COV_BUILD_DIR",
    }
)

#: Directory, under cargo-llvm-cov's target directory, where its own runs build.
LLVM_COV_TARGET_SUBDIR = "llvm-cov-target"


def parse_show_env(output: str) -> dict[str, str]:
    """Return the ``KEY=value`` pairs printed by ``cargo llvm-cov show-env``.

    Each value is read with shell quoting, so a quoted value holding spaces is
    one word; an unquoted value holding spaces has its words rejoined with
    single spaces. Lines that are not a ``KEY=value`` pair are ignored.

    Raises
    ------
    ValueError
        If a value has unbalanced quotes, naming the key it belongs to.
    """
    pairs: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key.isidentifier():
            continue
        try:
            pairs[key] = " ".join(shlex.split(value))
        except ValueError as exc:
            msg = f"cannot parse the value of {key} printed by cargo llvm-cov show-env"
            raise ValueError(msg) from exc
    return pairs


def build_environment(
    printed: typ.Mapping[str, str], *, use_nextest: bool
) -> tuple[dict[str, str], Path]:
    """Return the build's environment and target directory from ``show-env``.

    The variables in ``SHOW_ENV_LEAKS`` are dropped, and the profile file is
    rebased into the target directory ``cargo llvm-cov`` builds in, because
    its value is part of what the compiler sees. ``show-env`` names profiles
    as nextest runs do, with a 32-character module signature; a plain
    ``cargo llvm-cov`` run uses the default one, so it is matched here.

    Raises
    ------
    ValueError
        If ``show-env`` reports a build directory other than the target
        directory, as a configured ``build.build-dir`` would: the build would
        then need a matching directory this mode does not set.
    """
    build_dir = printed.get("CARGO_LLVM_COV_BUILD_DIR")
    if build_dir is not None and build_dir != printed.get("CARGO_LLVM_COV_TARGET_DIR"):
        msg = (
            "build-only does not support a separate Cargo build directory "
            "(build.build-dir); cargo-llvm-cov reports "
            f"{build_dir!r} apart from its target directory"
        )
        raise ValueError(msg)
    env = {key: value for key, value in printed.items() if key not in SHOW_ENV_LEAKS}
    target_dir = (
        Path(printed.get("CARGO_LLVM_COV_TARGET_DIR", "target"))
        / LLVM_COV_TARGET_SUBDIR
    )
    profile_name = Path(env.get("LLVM_PROFILE_FILE", "")).name
    if profile_name and not use_nextest:
        profile_name = profile_name.replace("%32m", "%m")
    if profile_name:
        env["LLVM_PROFILE_FILE"] = str(target_dir / profile_name)
    return env, target_dir


@dataclasses.dataclass(frozen=True, slots=True)
class BuildSelection:
    """What a build-only run compiles: its runner, targets and feature flags."""

    use_nextest: bool
    all_targets: bool
    feature_args: tuple[str, ...]


def build_command(
    manifest_path: Path, target_dir: Path, selection: BuildSelection
) -> list[str]:
    """Return the cargo arguments that compile the test binaries and stop."""
    args = ["nextest", "run"] if selection.use_nextest else ["test"]
    args += [
        "--no-run",
        "--manifest-path",
        str(manifest_path),
        "--target-dir",
        str(target_dir),
        "--workspace",
    ]
    if selection.all_targets:
        args.append("--all-targets")
    elif not selection.use_nextest:
        # Plain `cargo llvm-cov` runs `cargo test --tests`, which leaves
        # examples uncompiled unless they are test targets; a bare
        # `cargo test --no-run` would compile them and could fail where the
        # coverage run succeeds. Nextest builds its own selection already.
        args.append("--tests")
    return [*args, *selection.feature_args]
