#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "typer>=0.12.3",
#   "plumbum>=1.8.2",
#   "uuid6>=2023.5.29",
# ]
# ///
"""
polythene — Temu podman for Codex

Two subcommands:

  polythene pull IMAGE
      Pull/export IMAGE into a per-UUID rootfs; prints the UUID to stdout.

  polythene exec UUID -- CMD [ARG...]
      Execute a command in the rootfs identified by UUID, trying bubblewrap -> proot -> chroot.
      No networking, no cgroups, no container runtime needed at exec-time.

Environment:
  POLYTHENE_STORE   Root directory for UUID rootfs (default: /var/tmp/polythene)
  POLYTHENE_VERBOSE If set (to any value), prints progress logs to stderr.

Podman env hardening (set automatically if unset):
  CONTAINERS_STORAGE_DRIVER=vfs
  CONTAINERS_EVENTS_BACKEND=file
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from uuid6 import uuid7

sys.path.append(str(Path(__file__).resolve().parents[4]))
from cmd_utils import run_cmd


# -------------------- Configuration --------------------

DEFAULT_STORE = Path(os.environ.get("POLYTHENE_STORE", "/var/tmp/polythene")).resolve()
VERBOSE = bool(os.environ.get("POLYTHENE_VERBOSE"))

IS_ROOT = os.geteuid() == 0

# Make Podman as “quiet and simple” as possible for nested/sandboxed execution.
os.environ.setdefault("CONTAINERS_STORAGE_DRIVER", "vfs")
os.environ.setdefault("CONTAINERS_EVENTS_BACKEND", "file")

app = typer.Typer(add_completion=False, help="polythene — Temu podman for Codex")


def log(msg: str) -> None:
    if VERBOSE:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", file=sys.stderr)


def ensure_cmd(name: str) -> None:
    try:
        _ = local[name]
    except Exception:
        typer.secho(
            f"Required command not found: {name}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(127)


def store_path_for(uuid: str, store: Path) -> Path:
    return (store / uuid).resolve()


def generate_uuid() -> str:
    return str(uuid7())


# -------------------- Image export (“pull”) --------------------


def export_rootfs(image: str, dest: Path) -> None:
    """Export a container image filesystem to dest/ via podman create+export."""
    ensure_cmd("podman")
    podman = local["podman"]
    tar = local["tar"]

    # Pull explicitly (keeps exec fully offline later)
    log(f"Pulling {image} …")
    run_cmd(podman["pull", image], fg=True)

    dest.mkdir(parents=True, exist_ok=False)

    # Create a stopped container to export its rootfs
    cid = run_cmd(podman["create", "--pull=never", image, "true"]).strip()
    try:
        log(f"Exporting rootfs of {cid} → {dest}")
        # Pipe: podman export CID | tar -C dest -x
        # plumbum pipes stream in FG without buffering the whole archive
        run_cmd(
            (podman["export", cid] | tar["-C", str(dest), "-x"]),
            fg=True,
        )
    finally:
        try:
            run_cmd(podman["rm", cid], fg=True)
        except ProcessExecutionError:
            pass

    # Metadata (best-effort, does not affect functionality)
    meta = dest / ".polythene-meta"
    try:
        meta.write_text(
            f"image={image}\ncreated={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


# -------------------- Execution backends --------------------


def _ensure_dirs(root: Path) -> None:
    # Make sure minimal paths exist for binding/tmpfs convenience
    for sub in ("dev", "tmp"):
        (root / sub).mkdir(exist_ok=True)


def _probe_bwrap_userns(bwrap, root: Path) -> List[str]:
    """Return userns flags if permitted; otherwise empty list."""
    try:
        # Quick probe: this tests unpriv userns availability (or setuid bwrap handles it).
        run_cmd(
            bwrap[
                "--unshare-user",
                "--uid",
                "0",
                "--gid",
                "0",
                "--bind",
                "/",
                "/",
                "true",
            ],
            fg=True,
        )
        return ["--unshare-user", "--uid", "0", "--gid", "0"]
    except Exception:
        return []


def _probe_bwrap_proc(bwrap, base_flags: List[str], root: Path) -> List[str]:
    """Return ['--proc', '/proc'] if allowed; else []."""
    try:
        cmd = bwrap[
            *base_flags,
            "--bind",
            str(root),
            "/",
            "--proc",
            "/proc",
            "true",
        ]
        run_cmd(cmd, fg=True)
        return ["--proc", "/proc"]
    except Exception:
        return []


def run_with_bwrap(root: Path, inner_cmd: str) -> Optional[int]:
    try:
        bwrap = local["bwrap"]
    except Exception:
        return None

    _ensure_dirs(root)

    # Build base flags and probe capabilities
    userns_flags = _probe_bwrap_userns(bwrap, root)
    base_flags = [*userns_flags, "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    proc_flags = _probe_bwrap_proc(bwrap, base_flags, root)

    # Viability probe (run 'true' inside the environment)
    probe = bwrap[
        *base_flags,
        "--bind",
        str(root),
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        *proc_flags,
        "--tmpfs",
        "/tmp",
        "--chdir",
        "/",
        "/bin/sh",
        "-c",
        "true",
    ]
    try:
        run_cmd(probe, fg=True)
    except ProcessExecutionError:
        return None

    log("Executing via bubblewrap")
    cmd = bwrap[
        *base_flags,
        "--bind",
        str(root),
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        *proc_flags,
        "--tmpfs",
        "/tmp",
        "--chdir",
        "/",
        "/bin/sh",
        "-lc",
        inner_cmd,
    ]
    return run_cmd(cmd, fg=True)  # returns exit code


def run_with_proot(root: Path, inner_cmd: str) -> Optional[int]:
    try:
        proot = local["proot"]
    except Exception:
        return None

    _ensure_dirs(root)

    # Viability probe
    try:
        run_cmd(proot["-R", str(root), "-0", "/bin/sh", "-c", "true"], fg=True)
    except ProcessExecutionError:
        return None

    log("Executing via proot")
    cmd = proot["-R", str(root), "-0", "/bin/sh", "-lc", inner_cmd]
    return run_cmd(cmd, fg=True)


def run_with_chroot(root: Path, inner_cmd: str) -> Optional[int]:
    try:
        chroot = local["chroot"]
    except Exception:
        return None

    # Viability probe
    try:
        run_cmd(chroot[str(root), "/bin/sh", "-c", "true"], fg=True)
    except ProcessExecutionError:
        return None

    log("Executing via chroot")
    cmd = chroot[
        str(root),
        "/bin/sh",
        "-lc",
        f"export PATH=/bin:/sbin:/usr/bin:/usr/sbin; {inner_cmd}",
    ]
    return run_cmd(cmd, fg=True)


# -------------------- CLI commands --------------------


@app.command("pull")
def cmd_pull(
    image: str = typer.Argument(
        ..., help="Image reference, e.g. docker.io/library/busybox:latest"
    ),
    store: Path = typer.Option(
        DEFAULT_STORE,
        "--store",
        "-s",
        help="Directory to store UUID rootfs trees",
        dir_okay=True,
        file_okay=False,
    ),
) -> None:
    """
    Pull IMAGE and export its filesystem into a new UUIDv7 directory under STORE.
    Prints the UUID on stdout.
    """
    ensure_cmd("podman")
    store.mkdir(parents=True, exist_ok=True)
    uid = generate_uuid()
    root = store_path_for(uid, store)
    try:
        export_rootfs(image, root)
    except FileExistsError:
        # Incredibly unlikely with v7; regenerate once
        uid = generate_uuid()
        root = store_path_for(uid, store)
        export_rootfs(image, root)

    # Ensure minimal dirs for later exec
    _ensure_dirs(root)

    print(uid)


@app.command("exec")
def cmd_exec(
    uuid: str = typer.Argument(
        ..., help="UUID of the exported filesystem (from `polythene pull`)"
    ),
    cmd: List[str] = typer.Argument(
        ..., help="Command and arguments to execute inside the rootfs"
    ),
    store: Path = typer.Option(
        DEFAULT_STORE,
        "--store",
        "-s",
        help="Directory where UUID rootfs trees are stored",
        dir_okay=True,
        file_okay=False,
    ),
) -> None:
    """
    Execute CMD within the filesystem identified by UUID, using bwrap → proot → chroot fallback.
    The command's exit status is propagated.
    """
    if not cmd:
        typer.secho("No command provided", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    root = store_path_for(uuid, store)
    if not root.is_dir():
        typer.secho(
            f"No such UUID rootfs: {uuid} ({root})", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    inner_cmd = " ".join(shlex.quote(x) for x in cmd)

    # Try each backend. Only fall through if the backend is not viable;
    # if viable, we return with that backend's exit code (even if non-zero).
    runners = (
        (run_with_bwrap, run_with_proot)
        if not IS_ROOT
        else (run_with_bwrap, run_with_proot, run_with_chroot)
    )
    for runner in runners:
        try:
            rc = runner(root, inner_cmd)
        except ProcessExecutionError as e:
            # Runner executed and failed; propagate its exit code
            raise typer.Exit(e.retcode)
        if rc is not None:
            raise typer.Exit(rc)

    typer.secho(
        "All isolation modes unavailable (bwrap/proot/chroot).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(126)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
