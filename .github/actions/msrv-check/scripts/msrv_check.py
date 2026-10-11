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

`cargo +<version>` selects the toolchain over any `rust-toolchain.toml`, so
that file needs no removal.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import typing as typ
from pathlib import Path

TRUTHY = frozenset({"true", "1", "yes", "on"})
DEFAULT_ARGS = "--lib --locked --all-features"
CONFIG_FILES = (".cargo/config.toml", ".cargo/config")


class MsrvError(Exception):
    """A condition the job reports by name instead of failing obscurely."""


def _load(path: Path) -> dict[str, object]:
    """Return the decoded manifest at *path*."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        message = f"manifest {path} does not exist"
        raise MsrvError(message) from None
    except tomllib.TOMLDecodeError as error:
        message = f"manifest {path} is not valid TOML: {error}"
        raise MsrvError(message) from error


def _table(document: object, *keys: str) -> dict[str, object]:
    """Return the nested table at *keys*, or an empty one."""
    node = document
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _directories(manifest: Path, root: Path) -> list[Path]:
    """Return the manifest's directory and each ancestor up to *root*."""
    directories = [manifest.parent]
    while directories[-1] != root and directories[-1] != directories[-1].parent:
        directories.append(directories[-1].parent)
    return directories


def _workspace_rust_version(manifest: Path, root: Path) -> str | None:
    """Return the `[workspace.package]` rust-version of the nearest workspace."""
    for directory in _directories(manifest, root):
        candidate = directory / "Cargo.toml"
        if candidate.is_file():
            value = _table(_load(candidate), "workspace", "package").get("rust-version")
            if isinstance(value, str):
                return value
    return None


def read_rust_version(manifest: Path, root: Path) -> str:
    """Return the manifest's rust-version, following workspace inheritance.

    Raises
    ------
    MsrvError
        If the manifest, or the workspace it inherits from, declares none.
    """
    document = _load(manifest)
    declared = _table(document, "package").get("rust-version")
    if isinstance(declared, str):
        return declared
    shared = _workspace_rust_version(manifest, root)
    if shared is not None:
        return shared
    message = (
        f"{manifest} declares no rust-version, directly or through its "
        "workspace, so there is no MSRV to check"
    )
    raise MsrvError(message)


def drop_cargo_config(root: Path, manifest: Path) -> list[Path]:
    """Remove the cargo config files from the checkout; return what was removed."""
    removed = [
        path
        for directory in _directories(manifest, root)
        for name in CONFIG_FILES
        if (path := directory / name).is_file()
    ]
    for path in removed:
        path.unlink()
    return removed


def _run(command: list[str], env: dict[str, str], cwd: Path) -> int:
    """Run *command* and return its exit status."""
    print("+", " ".join(command), flush=True)
    # Stdlib only: the job runs on the runner's python3 with no plumbum install.
    return subprocess.run(command, env=env, cwd=cwd, check=False).returncode  # noqa: TID251, S603


class Settings(typ.NamedTuple):
    """The action's inputs, resolved against their defaults."""

    root: Path
    manifest: Path
    cargo_args: list[str]
    keep_config: bool


def read_settings(env: dict[str, str]) -> Settings:
    """Resolve the action inputs from *env*."""
    root = Path(env.get("INPUT_WORKING_DIRECTORY") or ".").resolve()
    keep = (env.get("INPUT_KEEP_CARGO_CONFIG") or "false").strip().lower()
    return Settings(
        root=root,
        manifest=(root / (env.get("INPUT_MANIFEST_PATH") or "Cargo.toml")).resolve(),
        cargo_args=(env.get("INPUT_CARGO_ARGS") or DEFAULT_ARGS).split(),
        keep_config=keep in TRUTHY,
    )


def _prepare(settings: Settings, env: dict[str, str]) -> None:
    """Drop the cargo config and empty RUSTFLAGS unless the config is kept."""
    if settings.keep_config:
        return
    for path in drop_cargo_config(settings.root, settings.manifest):
        print(
            f"::notice title=msrv-check::dropped {path} from the runner checkout",
            flush=True,
        )
    env["RUSTFLAGS"] = ""


def _publish_version(env: dict[str, str], version: str) -> None:
    """Write the `msrv` output when the runner provides an output file."""
    output = env.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"msrv={version}\n")


def main(environ: dict[str, str] | None = None) -> int:
    """Run the check; return the process exit status."""
    env = dict(os.environ if environ is None else environ)
    settings = read_settings(env)
    try:
        version = read_rust_version(settings.manifest, settings.root)
    except MsrvError as error:
        print(f"::error title=msrv-check::{error}", file=sys.stderr, flush=True)
        return 1
    _prepare(settings, env)
    _publish_version(env, version)
    install = ["rustup", "toolchain", "install", version, "--profile", "minimal"]
    status = _run([*install, "--no-self-update"], env, settings.root)
    if status != 0:
        return status
    check = ["cargo", f"+{version}", "check", f"--manifest-path={settings.manifest}"]
    return _run([*check, *settings.cargo_args], env, settings.root)


if __name__ == "__main__":
    sys.exit(main())
