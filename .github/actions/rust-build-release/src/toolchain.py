"""Helpers for Rust toolchain management."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils import ensure_allowed_executable, run_validated

TOOLCHAIN_VERSION_FILE = Path(__file__).resolve().parents[1] / "TOOLCHAIN_VERSION"


def read_default_toolchain() -> str:
    """Return the repository's default Rust toolchain."""
    return TOOLCHAIN_VERSION_FILE.read_text(encoding="utf-8").strip()


def toolchain_triple(toolchain: str) -> str | None:
    """Return the target triple embedded in *toolchain*, if present."""
    parts = toolchain.split("-")
    return "-".join(parts[-4:]) if len(parts) >= 4 else None


def configure_windows_linkers(toolchain_name: str, target: str, rustup: str) -> None:
    """Ensure Windows GNU builds use consistent linker binaries."""
    if sys.platform != "win32":
        return

    rustup_exec = ensure_allowed_executable(rustup, ("rustup", "rustup.exe"))
    triple = toolchain_triple(toolchain_name)
    if triple:
        try:
            rustc_path_result = run_validated(
                rustup_exec,
                ["which", "rustc", "--toolchain", toolchain_name],
                allowed_names=("rustup", "rustup.exe"),
                method="run",
            )
        except FileNotFoundError:
            pass
        else:
            if rustc_path_result.returncode != 0:
                raise subprocess.CalledProcessError(
                    rustc_path_result.returncode,
                    [
                        rustup_exec,
                        "which",
                        "rustc",
                        "--toolchain",
                        toolchain_name,
                    ],
                    output=rustc_path_result.stdout,
                    stderr=rustc_path_result.stderr,
                )
            if rustc_stdout := rustc_path_result.stdout.strip():
                toolchain_root = Path(rustc_stdout).resolve().parent.parent
                linker_path = (
                    toolchain_root / "lib" / "rustlib" / triple / "bin" / "gcc.exe"
                )
                if linker_path.exists():
                    env_key = f"CARGO_TARGET_{triple.upper().replace('-', '_')}_LINKER"
                    os.environ.setdefault(env_key, str(linker_path))

    if target.endswith("-pc-windows-gnu"):
        arch = target.split("-", 1)[0]
        for linker_name in (
            f"{arch}-w64-mingw32-gcc",
            f"{arch}-w64-mingw32-clang",
            f"{arch}-w64-mingw32-clang++",
        ):
            if cross_linker := shutil.which(linker_name):
                env_key = f"CARGO_TARGET_{target.upper().replace('-', '_')}_LINKER"
                os.environ.setdefault(env_key, cross_linker)
                break
