"""Helpers for Rust toolchain management."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
import typing as typ
from pathlib import Path

from utils import ensure_allowed_executable, run_validated

TOOLCHAIN_VERSION_FILE = Path(__file__).resolve().parents[1] / "TOOLCHAIN_VERSION"


def read_default_toolchain() -> str:
    """Return the repository's default Rust toolchain."""
    return TOOLCHAIN_VERSION_FILE.read_text(encoding="utf-8").strip()


def _resolve_manifest_path(project_dir: Path, manifest_path: Path) -> Path:
    """Resolve *manifest_path* relative to *project_dir* when needed."""
    candidate = manifest_path.expanduser()
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    return candidate.resolve()


def _strip_optional(value: str | None) -> str | None:
    """Return a trimmed string or ``None`` when the input is blank."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _parse_legacy_toolchain_file(raw: str) -> str | None:
    """Return the first non-blank, non-comment line from a legacy toolchain file."""
    for line in raw.splitlines():
        if channel := _strip_optional(line.partition("#")[0]):
            return channel
    return None


def _extract_toml_channel(data: dict) -> str | None:
    """Return ``[toolchain].channel`` from parsed TOML data, if any."""
    toolchain = data.get("toolchain")
    if not isinstance(toolchain, dict):
        return None
    channel = toolchain.get("channel")
    if isinstance(channel, str):
        return _strip_optional(channel)
    return None


def _parse_toolchain_file(path: Path) -> str | None:
    """Return the declared toolchain channel from *path*, if any."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        if path.name != "rust-toolchain":
            return None
        return _parse_legacy_toolchain_file(raw)

    return _extract_toml_channel(data)


def _iter_toolchain_search_dirs(
    start: Path, stop_at: Path | None = None
) -> typ.Iterator[Path]:
    """Yield directories to search for repository toolchain declarations.

    Stops at the first ``.git`` directory encountered, at the filesystem root,
    or at *stop_at* (inclusive) when provided.
    """
    search_dir = start.resolve()
    stop = stop_at.resolve() if stop_at is not None else None
    while True:
        yield search_dir
        if stop is not None and search_dir == stop:
            return
        if (search_dir / ".git").exists():
            return
        parent = search_dir.parent
        if parent == search_dir:
            return
        search_dir = parent


def read_repo_toolchain(project_dir: Path, manifest_path: Path) -> str | None:
    """Return the repo-declared toolchain nearest the target manifest, if any."""
    resolved_manifest = _resolve_manifest_path(project_dir, manifest_path)
    for directory in _iter_toolchain_search_dirs(resolved_manifest.parent, project_dir):
        for filename in ("rust-toolchain.toml", "rust-toolchain"):
            if toolchain := _parse_toolchain_file(directory / filename):
                return toolchain
    return None


def _section_rust_version(section: object) -> str | None:
    """Return ``rust-version`` from a ``[package]``-like TOML mapping, if present."""
    if not isinstance(section, dict):
        return None
    mapping = typ.cast("dict[str, object]", section)
    rust_version = mapping.get("rust-version")
    if isinstance(rust_version, str):
        return _strip_optional(rust_version)
    return None


def _workspace_rust_version(manifest_data: dict) -> str | None:
    """Return ``rust-version`` from ``[workspace.package]``, if declared."""
    workspace = manifest_data.get("workspace")
    if not isinstance(workspace, dict):
        return None
    return _section_rust_version(workspace.get("package"))


def read_manifest_rust_version(project_dir: Path, manifest_path: Path) -> str | None:
    """Return ``rust-version`` from the manifest when it is declared."""
    try:
        manifest_data = tomllib.loads(
            _resolve_manifest_path(project_dir, manifest_path).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, tomllib.TOMLDecodeError):
        return None

    return _section_rust_version(
        manifest_data.get("package")
    ) or _workspace_rust_version(manifest_data)


def resolve_requested_toolchain(
    explicit_toolchain: str | None,
    *,
    project_dir: Path,
    manifest_path: Path,
    fallback_toolchain: str,
) -> str:
    """Resolve the toolchain using explicit input, repo config, MSRV, then fallback."""
    if toolchain := _strip_optional(explicit_toolchain):
        return toolchain
    if repo_toolchain := read_repo_toolchain(project_dir, manifest_path):
        return repo_toolchain
    if rust_version := read_manifest_rust_version(project_dir, manifest_path):
        return rust_version
    return fallback_toolchain


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
        rustup_args = ["which", "rustc", "--toolchain", toolchain_name]
        rustup_cmd = [rustup_exec, *rustup_args]
        try:
            rustc_path_result = run_validated(
                rustup_exec,
                rustup_args,
                allowed_names=("rustup", "rustup.exe"),
                method="run",
            )
        except FileNotFoundError:
            pass
        else:
            if rustc_path_result.returncode != 0:
                raise subprocess.CalledProcessError(
                    rustc_path_result.returncode,
                    rustup_cmd,
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
