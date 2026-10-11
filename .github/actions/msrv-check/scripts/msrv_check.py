#!/usr/bin/env python3
"""Check a crate on its declared minimum supported Rust version (MSRV).

The version is read from the manifest, never written into the workflow, so the
manifest stays the only place it is declared. The script installs that
toolchain and runs one `cargo check`. It uses only the standard library, so the
job needs no Python tooling beyond the runner's `python3` (3.11 or newer, for
`tomllib`).

Two things make a stable MSRV check fail for reasons unrelated to the MSRV, and
the script removes both in the runner's checkout only (nothing is committed):

* `.cargo/config.toml` in a development repository often names nightly-only
  keys (such as a codegen backend), or a linker that this job does not install;
* an inherited `RUSTFLAGS` replaces the config's rustflags, so the script sets
  it empty.

Cargo reads `.cargo/config.toml` from the working directory and every parent
up to the filesystem root, so the script removes the files from the manifest's
directory up to the checkout (`GITHUB_WORKSPACE`), and clears both `RUSTFLAGS`
and `CARGO_ENCODED_RUSTFLAGS`, which Cargo reads first. `cargo +<version>`
selects the toolchain over any `rust-toolchain.toml`, so that file needs no
removal.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tomllib
import typing as typ
from pathlib import Path

TRUTHY = frozenset({"true", "1", "yes", "on"})
DEFAULT_ARGS = "--lib --locked --all-features"
CONFIG_FILES = (".cargo/config.toml", ".cargo/config")
INHERIT = {"workspace": True}


class MsrvError(Exception):
    """A condition the job reports by name instead of failing obscurely.

    Raised for an unreadable or invalid manifest, a manifest that declares no
    rust-version, and malformed action inputs. `main` turns it into a
    GitHub Actions error annotation and a non-zero exit status.
    """


def _load(path: Path) -> dict[str, object]:
    """Return the decoded manifest at *path*.

    Parameters
    ----------
    path:
        The Cargo manifest to read.

    Returns
    -------
    dict[str, object]
        The decoded TOML document.

    Raises
    ------
    MsrvError
        If the file is missing, unreadable, not UTF-8 or not valid TOML.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        message = f"manifest {path} does not exist"
        raise MsrvError(message) from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        message = f"manifest {path} cannot be read: {error}"
        raise MsrvError(message) from error


def _table(document: object, *keys: str) -> dict[str, object]:
    """Return the nested table at *keys*, or an empty one."""
    node = document
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _directories(start: Path, boundary: Path) -> list[Path]:
    """Return *start* and each ancestor up to *boundary*, inclusive.

    The walk also stops at the filesystem root, so a boundary that is not an
    ancestor of *start* cannot run away.
    """
    directories = [start]
    while directories[-1] != boundary and directories[-1] != directories[-1].parent:
        directories.append(directories[-1].parent)
    return directories


def _workspace_rust_version(manifest: Path, boundary: Path) -> str | None:
    """Return the `[workspace.package]` rust-version of the nearest workspace."""
    for directory in _directories(manifest.parent, boundary):
        candidate = directory / "Cargo.toml"
        if candidate.is_file():
            value = _table(_load(candidate), "workspace", "package").get("rust-version")
            if isinstance(value, str):
                return value
    return None


def read_rust_version(manifest: Path, boundary: Path) -> str:
    """Return the manifest's rust-version, following explicit workspace inheritance.

    A member inherits the workspace value only when it writes
    `rust-version.workspace = true`, as Cargo does; a member that omits the key
    declares no MSRV even when its workspace defines one.

    Parameters
    ----------
    manifest:
        The Cargo manifest that should declare `rust-version`.
    boundary:
        The directory above which no workspace manifest is searched.

    Returns
    -------
    str
        The declared minimum supported Rust version.

    Raises
    ------
    MsrvError
        If the manifest declares none, or inherits one its workspace lacks.
    """
    declared = _table(_load(manifest), "package").get("rust-version")
    if isinstance(declared, str):
        return declared
    if declared == INHERIT:
        shared = _workspace_rust_version(manifest, boundary)
        if shared is not None:
            return shared
    message = (
        f"{manifest} declares no rust-version (directly, or through "
        "`rust-version.workspace = true` with a workspace value), so there is "
        "no MSRV to check"
    )
    raise MsrvError(message)


def drop_cargo_config(start: Path, boundary: Path) -> list[Path]:
    """Remove the cargo config files Cargo would read; return what was removed.

    Parameters
    ----------
    start:
        The directory Cargo runs from, or the manifest's directory.
    boundary:
        The checkout root; nothing above it is touched.

    Returns
    -------
    list[Path]
        The files removed, from *start* upward.
    """
    removed = [
        path
        for directory in _directories(start, boundary)
        for name in CONFIG_FILES
        if (path := directory / name).is_file()
    ]
    for path in removed:
        path.unlink()
    return removed


def _run(command: list[str], env: dict[str, str], cwd: Path, label: str) -> int:
    """Run *command* and return its exit status.

    The log names *label*, not the command line: the cargo arguments come from
    a caller-controlled input and are not echoed.
    """
    print(f"+ {label}", flush=True)
    # Stdlib only: the job runs on the runner's python3 with no plumbum install.
    return subprocess.run(command, env=env, cwd=cwd, check=False).returncode  # noqa: TID251, S603


class Settings(typ.NamedTuple):
    """The action's inputs, resolved against their defaults."""

    root: Path
    boundary: Path
    manifest: Path
    cargo_args: list[str]
    keep_config: bool


def read_settings(env: dict[str, str]) -> Settings:
    """Resolve the action inputs from *env*.

    Parameters
    ----------
    env:
        The environment the action runs in.

    Returns
    -------
    Settings
        The inputs, defaults applied. The checkout boundary is
        `GITHUB_WORKSPACE` when that is an ancestor of the working directory,
        otherwise the working directory itself.

    Raises
    ------
    MsrvError
        If `cargo-args` has unbalanced quotes.
    """
    root = Path(env.get("INPUT_WORKING_DIRECTORY") or ".").resolve()
    workspace = (
        Path(env["GITHUB_WORKSPACE"]).resolve() if env.get("GITHUB_WORKSPACE") else root
    )
    boundary = workspace if workspace in (root, *root.parents) else root
    try:
        cargo_args = shlex.split(env.get("INPUT_CARGO_ARGS") or DEFAULT_ARGS)
    except ValueError as error:
        message = f"cargo-args is not valid shell quoting: {error}"
        raise MsrvError(message) from error
    keep = (env.get("INPUT_KEEP_CARGO_CONFIG") or "false").strip().lower()
    return Settings(
        root=root,
        boundary=boundary,
        manifest=(root / (env.get("INPUT_MANIFEST_PATH") or "Cargo.toml")).resolve(),
        cargo_args=cargo_args,
        keep_config=keep in TRUTHY,
    )


def _prepare(settings: Settings, env: dict[str, str]) -> None:
    """Drop the cargo config and inherited rustflags unless the config is kept."""
    if settings.keep_config:
        return
    for path in drop_cargo_config(settings.manifest.parent, settings.boundary):
        print(
            f"::notice title=msrv-check::dropped {path} from the runner checkout",
            flush=True,
        )
    env["RUSTFLAGS"] = ""
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)


def _publish_version(env: dict[str, str], version: str) -> None:
    """Write the `msrv` output when the runner provides an output file."""
    output = env.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"msrv={version}\n")


def main(environ: dict[str, str] | None = None) -> int:
    """Run the check.

    Parameters
    ----------
    environ:
        The environment to read the inputs from; the process environment when
        omitted. Tests pass their own.

    Returns
    -------
    int
        0 when the crate builds on its MSRV; 1 for an input or manifest error;
        otherwise the failing toolchain command's exit status.
    """
    env = dict(os.environ if environ is None else environ)
    try:
        settings = read_settings(env)
        version = read_rust_version(settings.manifest, settings.boundary)
    except MsrvError as error:
        print(f"::error title=msrv-check::{error}", file=sys.stderr, flush=True)
        return 1
    _prepare(settings, env)
    _publish_version(env, version)
    install = ["rustup", "toolchain", "install", version, "--profile", "minimal"]
    status = _run(
        [*install, "--no-self-update"], env, settings.root, f"install {version}"
    )
    if status != 0:
        return status
    check = ["cargo", f"+{version}", "check", f"--manifest-path={settings.manifest}"]
    return _run(
        [*check, *settings.cargo_args], env, settings.root, f"cargo +{version} check"
    )


if __name__ == "__main__":
    sys.exit(main())
