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
polythene — Temu podman for Codex.

Two subcommands:

  polythene pull IMAGE
      Pull/export IMAGE into a per-UUID rootfs; prints the UUID to stdout.

  polythene exec UUID -- CMD [ARG...]
      Execute a command in the rootfs identified by UUID, trying
      bubblewrap -> proot -> chroot. No networking, no cgroups,
      no container runtime needed at exec-time.

Environment:
  POLYTHENE_STORE   Root directory for UUID rootfs (default: /var/tmp/polythene)
  POLYTHENE_VERBOSE If set (to any value), prints progress logs to stderr.

Podman environment hardening (set automatically if unset):
  CONTAINERS_STORAGE_DRIVER=vfs
  CONTAINERS_EVENTS_BACKEND=file
"""

from __future__ import annotations

import contextlib
import os
import shlex
import sys
import tempfile
import time
import types
import typing as typ
from pathlib import Path

import typer
from plumbum.commands.processes import ProcessExecutionError
from uuid6 import uuid7

if typ.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand

    from .script_utils import ensure_directory, get_command, run_cmd
else:
    try:
        from .script_utils import ensure_directory, get_command, run_cmd
    except ImportError:  # pragma: no cover - fallback for direct execution
        import importlib.util
        import sys

        _PKG_DIR = Path(__file__).resolve().parent
        _PKG_NAME = "linux_packages_scripts"
        pkg_module = sys.modules.get(_PKG_NAME)
        if pkg_module is None:
            pkg_module = types.ModuleType(_PKG_NAME)
            pkg_module.__path__ = [str(_PKG_DIR)]  # type: ignore[attr-defined]
            sys.modules[_PKG_NAME] = pkg_module
        if not hasattr(pkg_module, "load_sibling"):
            spec = importlib.util.spec_from_file_location(
                _PKG_NAME, _PKG_DIR / "__init__.py"
            )
            if spec is None or spec.loader is None:
                raise ImportError(name="script_utils") from None
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PKG_NAME] = module
            spec.loader.exec_module(module)
            pkg_module = module

        load_sibling = typ.cast(
            "typ.Callable[[str], types.ModuleType]", pkg_module.load_sibling
        )
        helpers = typ.cast("typ.Any", load_sibling("script_utils"))
        ensure_directory = helpers.ensure_directory
        get_command = helpers.get_command
        run_cmd = helpers.run_cmd


# -------------------- Configuration --------------------

CONTAINER_TMP = Path(tempfile.gettempdir())
_DEFAULT_STORE_FALLBACK = Path(tempfile.gettempdir()) / "polythene"
DEFAULT_STORE = Path(
    os.environ.get("POLYTHENE_STORE", str(_DEFAULT_STORE_FALLBACK))
).resolve()
VERBOSE = bool(os.environ.get("POLYTHENE_VERBOSE"))

IS_ROOT = os.geteuid() == 0

# Make Podman as “quiet and simple” as possible for nested/sandboxed execution.
os.environ.setdefault("CONTAINERS_STORAGE_DRIVER", "vfs")
os.environ.setdefault("CONTAINERS_EVENTS_BACKEND", "file")

app = typer.Typer(add_completion=False, help="polythene — Temu podman for Codex")

ExecArgsFn = typ.Callable[[str], list[str]]

IMAGE_ARGUMENT = typer.Argument(
    ..., help="Image reference, e.g. docker.io/library/busybox:latest"
)
PULL_STORE_OPTION = typer.Option(
    DEFAULT_STORE,
    "--store",
    "-s",
    help="Directory to store UUID rootfs trees",
    dir_okay=True,
    file_okay=False,
)
PULL_TIMEOUT_OPTION = typer.Option(
    None,
    "--timeout",
    "-t",
    help="Timeout in seconds for pull and export commands",
)

UUID_ARGUMENT = typer.Argument(
    ..., help="UUID of the exported filesystem (from `polythene pull`)"
)
CMD_ARGUMENT = typer.Argument(
    ..., help="Command and arguments to execute inside the rootfs"
)
EXEC_STORE_OPTION = typer.Option(
    DEFAULT_STORE,
    "--store",
    "-s",
    help="Directory where UUID rootfs trees are stored",
    dir_okay=True,
    file_okay=False,
)
EXEC_TIMEOUT_OPTION = typer.Option(
    None,
    "--timeout",
    "-t",
    help="Timeout in seconds for command execution",
)


def log(msg: str) -> None:
    """Print ``msg`` to stderr with a timestamp when verbose mode is enabled."""
    if VERBOSE:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", file=sys.stderr)


def store_path_for(uuid: str, store: Path) -> Path:
    """Return the absolute path for ``uuid`` under ``store``."""
    return (store / uuid).resolve()


def generate_uuid() -> str:
    """Generate a UUID for a new root filesystem."""
    return str(uuid7())


# -------------------- Image export (“pull”) --------------------


def export_rootfs(image: str, dest: Path, *, timeout: int | None = None) -> None:
    """Export a container image filesystem to dest/ via podman create+export."""
    podman = get_command("podman")
    tar = get_command("tar")

    # Pull explicitly (keeps exec fully offline later)
    log(f"Pulling {image} …")
    try:
        run_cmd(podman["pull", image], fg=True, timeout=timeout)
    except ProcessExecutionError as exc:
        typer.secho(
            f"Failed to pull image {image}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(exc.retcode) from exc

    ensure_directory(dest, exist_ok=False)

    # Create a stopped container to export its rootfs
    try:
        create_result = run_cmd(
            podman["create", "--pull=never", image, "true"],
            timeout=timeout,
        )
        cid_output = (
            create_result[1] if isinstance(create_result, tuple) else create_result
        )
        cid = str(cid_output).strip()
    except ProcessExecutionError as exc:
        typer.secho(
            f"Failed to create container from {image}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(exc.retcode) from exc
    try:
        log(f"Exporting rootfs of {cid} → {dest}")
        # Pipe: podman export CID | tar -C dest -x
        # plumbum pipes stream in FG without buffering the whole archive
        run_cmd(
            (podman["export", cid] | tar["-C", str(dest), "-x"]),
            fg=True,
            timeout=timeout,
        )
    finally:
        with contextlib.suppress(ProcessExecutionError):
            run_cmd(podman["rm", cid], fg=True, timeout=timeout)

    # Metadata (best-effort, does not affect functionality)
    meta = dest / ".polythene-meta"
    with contextlib.suppress(Exception):
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta.write_text(
            f"image={image}\ncreated={timestamp}\n",
            encoding="utf-8",
        )


# -------------------- Execution backends --------------------


def _ensure_dirs(root: Path) -> None:
    # Make sure minimal paths exist for binding/tmpfs convenience
    for sub in ("dev", "tmp"):
        ensure_directory(root / sub)


def _probe_bwrap_userns(
    bwrap: BaseCommand, root: Path, *, timeout: int | None
) -> list[str]:
    """Return userns flags if permitted; otherwise empty list."""
    try:
        # Quick probe to test unpriv userns availability (or setuid bwrap handles it).
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
            timeout=timeout,
        )
    except (ProcessExecutionError, typer.Exit, OSError) as exc:
        log(f"User namespace probe failed: {exc}")
        return []
    else:
        return ["--unshare-user", "--uid", "0", "--gid", "0"]


def _probe_bwrap_proc(
    bwrap: BaseCommand,
    base_flags: list[str],
    root: Path,
    *,
    timeout: int | None,
) -> list[str]:
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
        run_cmd(cmd, fg=True, timeout=timeout)
    except (ProcessExecutionError, typer.Exit, OSError):
        return []
    else:
        return ["--proc", "/proc"]


def _build_bwrap_flags(
    bwrap: BaseCommand,
    root: Path,
    *,
    timeout: int | None,
) -> tuple[list[str], list[str]]:
    userns_flags = _probe_bwrap_userns(bwrap, root, timeout=timeout)
    base_flags = [*userns_flags, "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    proc_flags = _probe_bwrap_proc(bwrap, base_flags, root, timeout=timeout)
    return base_flags, proc_flags


def _run_with_tool(
    root: Path,
    inner_cmd: str,
    tool_name: str,
    tool_cmd: BaseCommand,
    probe_args: list[str],
    exec_args_fn: ExecArgsFn,
    *,
    ensure_dirs: bool = True,
    timeout: int | None = None,
) -> int | None:
    """Probe *tool_name* and execute *inner_cmd*, returning its exit status.

    Non-zero exits propagate to callers as ``typer.Exit(retcode)`` via the
    surrounding ``cmd_exec`` handler.
    """
    if ensure_dirs:
        _ensure_dirs(root)

    probe_cmd = tool_cmd[tuple(probe_args)]
    try:
        run_cmd(probe_cmd, fg=True, timeout=timeout)
    except (ProcessExecutionError, typer.Exit, OSError):
        return None

    log(f"Executing via {tool_name}")
    exec_cmd = tool_cmd[tuple(exec_args_fn(inner_cmd))]
    result = run_cmd(exec_cmd, fg=True, timeout=timeout)
    return int(result) if result is not None else 0


def run_with_bwrap(
    root: Path, inner_cmd: str, timeout: int | None = None
) -> int | None:
    """Attempt to execute ``inner_cmd`` inside ``root`` using bubblewrap."""
    try:
        bwrap = get_command("bwrap")
    except typer.Exit:
        return None
    base_flags, proc_flags = _build_bwrap_flags(bwrap, root, timeout=timeout)

    probe_args = [
        *base_flags,
        "--bind",
        str(root),
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        *proc_flags,
        "--tmpfs",
        str(CONTAINER_TMP),
        "--chdir",
        "/",
        "/bin/sh",
        "-c",
        "true",
    ]

    def exec_args(cmd: str) -> list[str]:
        return [
            *base_flags,
            "--bind",
            str(root),
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            *proc_flags,
            "--tmpfs",
            str(CONTAINER_TMP),
            "--chdir",
            "/",
            "/bin/sh",
            "-lc",
            cmd,
        ]

    return _run_with_tool(
        root,
        inner_cmd,
        "bubblewrap",
        bwrap,
        probe_args,
        exec_args,
        timeout=timeout,
    )


def run_with_proot(
    root: Path, inner_cmd: str, timeout: int | None = None
) -> int | None:
    """Attempt to execute ``inner_cmd`` inside ``root`` using proot."""
    try:
        proot = get_command("proot")
    except typer.Exit:
        return None

    probe_args = ["-R", str(root), "-0", "/bin/sh", "-c", "true"]

    def exec_args(cmd: str) -> list[str]:
        return ["-R", str(root), "-0", "/bin/sh", "-lc", cmd]

    return _run_with_tool(
        root,
        inner_cmd,
        "proot",
        proot,
        probe_args,
        exec_args,
        timeout=timeout,
    )


def run_with_chroot(
    root: Path, inner_cmd: str, timeout: int | None = None
) -> int | None:
    """Attempt to execute ``inner_cmd`` inside ``root`` using chroot."""
    try:
        chroot = get_command("chroot")
    except typer.Exit:
        return None

    probe_args = [str(root), "/bin/sh", "-c", "true"]

    def exec_args(cmd: str) -> list[str]:
        return [
            str(root),
            "/bin/sh",
            "-lc",
            f"export PATH=/bin:/sbin:/usr/bin:/usr/sbin; {cmd}",
        ]

    return _run_with_tool(
        root,
        inner_cmd,
        "chroot",
        chroot,
        probe_args,
        exec_args,
        ensure_dirs=False,
        timeout=timeout,
    )


# -------------------- CLI commands --------------------


@app.command("pull")
def cmd_pull(
    image: str = IMAGE_ARGUMENT,
    store: Path = PULL_STORE_OPTION,
    timeout: int | None = PULL_TIMEOUT_OPTION,
) -> None:
    """
    Pull IMAGE and export its filesystem into a new UUIDv7 directory under STORE.
    Prints the UUID on stdout.
    """
    ensure_directory(store)
    uid = generate_uuid()
    root = store_path_for(uid, store)
    try:
        export_rootfs(image, root, timeout=timeout)
    except FileExistsError:
        # Incredibly unlikely with v7; regenerate once
        uid = generate_uuid()
        root = store_path_for(uid, store)
        export_rootfs(image, root, timeout=timeout)

    # Ensure minimal dirs for later exec
    _ensure_dirs(root)

    print(uid)


@app.command("exec")
def cmd_exec(
    uuid: str = UUID_ARGUMENT,
    cmd: list[str] = CMD_ARGUMENT,
    store: Path = EXEC_STORE_OPTION,
    timeout: int | None = EXEC_TIMEOUT_OPTION,
) -> None:
    """Run ``CMD`` inside the UUID's rootfs with bwrap → proot → chroot fallback.

    The command's exit status is propagated.
    An optional timeout can abort long runs.
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
        (run_with_bwrap, run_with_proot, run_with_chroot)
        if IS_ROOT
        else (run_with_bwrap, run_with_proot)
    )
    for runner in runners:
        try:
            rc = runner(root, inner_cmd, timeout=timeout)
        except ProcessExecutionError as e:
            # Runner executed and failed; propagate its exit code
            raise typer.Exit(e.retcode) from e
        if rc is not None:
            if rc == 0:
                return
            raise typer.Exit(rc)

    typer.secho(
        "All isolation modes unavailable (bwrap/proot/chroot).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(126)


def main() -> None:
    """Invoke the Typer CLI entry point."""
    app()


if __name__ == "__main__":
    main()
