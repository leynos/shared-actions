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
from pathlib import Path

TRUTHY = frozenset({"true", "1", "yes", "on"})
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


def main(environ: dict[str, str] | None = None) -> int:
    """Run the check; return the process exit status."""
    env = dict(os.environ if environ is None else environ)
    root = Path(env.get("INPUT_WORKING_DIRECTORY") or ".").resolve()
    manifest = (root / (env.get("INPUT_MANIFEST_PATH") or "Cargo.toml")).resolve()
    cargo_args = (
        env.get("INPUT_CARGO_ARGS") or "--lib --locked --all-features"
    ).split()
    keep = (env.get("INPUT_KEEP_CARGO_CONFIG") or "false").strip().lower() in TRUTHY
    try:
        version = read_rust_version(manifest, root)
    except MsrvError as error:
        print(f"::error title=msrv-check::{error}", file=sys.stderr, flush=True)
        return 1
    if not keep:
        for path in drop_cargo_config(root, manifest):
            print(
                f"::notice title=msrv-check::dropped {path} from the runner checkout",
                flush=True,
            )
        env["RUSTFLAGS"] = ""
    output = env.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"msrv={version}\n")
    status = _run(
        [
            "rustup",
            "toolchain",
            "install",
            version,
            "--profile",
            "minimal",
            "--no-self-update",
        ],
        env,
        root,
    )
    if status != 0:
        return status
    return _run(
        ["cargo", f"+{version}", "check", f"--manifest-path={manifest}", *cargo_args],
        env,
        root,
    )


if __name__ == "__main__":
    sys.exit(main())
