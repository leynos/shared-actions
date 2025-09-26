#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=2.9,<4"]
# ///
"""Configure Windows gnullvm targets for x86_64 and aarch64 builds."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import platform
import re
import shutil
import socket
import stat
import sys
import tempfile
import textwrap
import time
import typing as typ
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

from cyclopts import App, Parameter

LLVM_MINGW_DEFAULT_VERSION = "20250924"
DEFAULT_TARGET = "x86_64-pc-windows-gnullvm"
LLVM_MINGW_DIR_PREFIX = "llvm-mingw-"
KNOWN_LLVM_MINGW_SHA256 = {
    "20250924": {
        "ucrt-x86_64": (
            "d2719495e711f5d07cb0781be5eb987ba549b07075578e34387dd127eb1341e8"
        ),
        "ucrt-arm64": (
            "0274ec5c504f440493ce85966dd3e48b857687b28d1ca64d7e0ec7fefe1bdeb3"
        ),
    }
}


@dataclasses.dataclass(frozen=True)
class TargetConfig:
    """Configuration metadata for a Windows gnullvm target."""

    clang_triplet: str


TARGET_CONFIGS: dict[str, TargetConfig] = {
    "x86_64-pc-windows-gnullvm": TargetConfig(
        clang_triplet="x86_64-w64-mingw32",
    ),
    "aarch64-pc-windows-gnullvm": TargetConfig(
        clang_triplet="aarch64-w64-mingw32",
    ),
}

app = App()


def _resolve_llvm_mingw_version() -> str:
    """Return the llvm-mingw release version to install."""
    return os.environ.get("RBR_LLVM_MINGW_VERSION", LLVM_MINGW_DEFAULT_VERSION)


def _detect_host_llvm_mingw_variant() -> str:
    """Return the llvm-mingw archive variant that matches the host."""
    arch_candidates = (
        os.environ.get("RBR_HOST_ARCH"),
        os.environ.get("RUNNER_ARCH"),
        os.environ.get("PROCESSOR_ARCHITECTURE"),
        platform.machine(),
    )

    for arch in arch_candidates:
        if not arch:
            continue
        normalized = arch.lower()
        if normalized in {"amd64", "x86_64"}:
            return "ucrt-x86_64"
        if normalized in {"arm64", "aarch64"}:
            return "ucrt-arm64"

    msg = (
        "Unable to determine host architecture for llvm-mingw; "
        "set RBR_LLVM_MINGW_VARIANT to override."
    )
    raise RuntimeError(msg)


def _resolve_llvm_mingw_variant() -> str:
    """Return the llvm-mingw archive variant to install."""
    if override := os.environ.get("RBR_LLVM_MINGW_VARIANT"):
        return override
    return _detect_host_llvm_mingw_variant()


def _expected_archive_sha256(version: str, variant: str) -> str | None:
    """Return the expected SHA-256 for the archive of *version*."""
    if override := os.environ.get("RBR_LLVM_MINGW_SHA256"):
        return override
    return KNOWN_LLVM_MINGW_SHA256.get(version, {}).get(variant)


def _sha256_file(path: Path) -> str:
    """Compute and return the SHA-256 digest for *path*."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def set_env(name: str, value: str) -> None:
    """Write an environment variable to GITHUB_ENV for subsequent steps."""
    if github_env := os.environ.get("GITHUB_ENV"):
        with Path(github_env).open("a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def add_to_path(path: str) -> None:
    """Add a directory to GITHUB_PATH for subsequent steps."""
    if github_path := os.environ.get("GITHUB_PATH"):
        with Path(github_path).open("a", encoding="utf-8") as fh:
            fh.write(f"{path}\n")


def download_and_unzip(
    url: str,
    dest: Path,
    *,
    retries: int = 3,
    expected_sha256: str | None = None,
) -> Path:
    """Download a zip file into *dest* and return the extracted directory."""
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / Path(url).name
    for attempt in range(1, retries + 1):
        try:
            print(f"Downloading {url} to {zip_path}... (attempt {attempt})")
            response = urllib.request.urlopen(url, timeout=60)  # noqa: S310
            with closing(response) as resp, zip_path.open("wb") as out_file:
                shutil.copyfileobj(resp, out_file)
        except (urllib.error.URLError, socket.timeout) as exc:  # noqa: UP041
            if attempt == retries:
                error_msg = f"Failed to download llvm-mingw archive from {url}"
                raise RuntimeError(error_msg) from exc
            sleep_seconds = min(2**attempt, 15)
            print(
                f"Download failed with {exc!r}; retrying in {sleep_seconds} seconds...",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
            continue
        break

    if expected_sha256 is None:
        msg = (
            "No expected SHA-256 provided for llvm-mingw archive; "
            "set RBR_LLVM_MINGW_SHA256 or use a known version"
        )
        raise RuntimeError(msg)

    actual_sha256 = _sha256_file(zip_path)
    if actual_sha256.lower() != expected_sha256.lower():
        msg = (
            "SHA-256 verification failed for llvm-mingw archive. "
            f"Expected {expected_sha256}, got {actual_sha256} from {url}."
        )
        raise RuntimeError(msg)

    if not zipfile.is_zipfile(zip_path):
        msg = f"Downloaded file at {zip_path} is not a valid zip archive."
        raise RuntimeError(msg)

    print(f"Extracting {zip_path} to {dest}...")
    base = dest.resolve()
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            target_path = (base / member.filename).resolve()
            mode = (member.external_attr >> 16) & 0o177777
            if stat.S_ISLNK(mode):
                msg = f"Archive contains unsupported symlink: {member.filename}"
                raise RuntimeError(msg)
            try:
                target_path.relative_to(base)
            except ValueError as exc:
                msg = f"Unsafe path in archive: {member.filename}"
                raise RuntimeError(msg) from exc
        zip_ref.extractall(base)

    extracted_dirs = [
        path
        for path in dest.iterdir()
        if path.is_dir() and path.name.startswith(LLVM_MINGW_DIR_PREFIX)
    ]
    if not extracted_dirs:
        msg = f"Zip file from {url} did not contain a directory."
        raise RuntimeError(msg)
    if len(extracted_dirs) > 1:
        dir_list = ", ".join(sorted(path.name for path in extracted_dirs))
        msg = (
            f"Zip file from {url} contained multiple directories with the expected"
            f" prefix: {dir_list}"
        )
        raise RuntimeError(msg)

    return extracted_dirs[0]


def _resolve_target(cli_target: str | None) -> str:
    """Resolve the requested gnullvm target from CLI or environment."""
    target_candidate = (
        cli_target
        or os.environ.get("RBR_TARGET")
        or os.environ.get("RBR_GNULLVM_TARGET")
        or DEFAULT_TARGET
    )
    if target_candidate not in TARGET_CONFIGS:
        supported_targets = ", ".join(sorted(TARGET_CONFIGS))
        msg = (
            f"Unsupported gnullvm target '{target_candidate}'. "
            f"Supported targets: {supported_targets}."
        )
        raise SystemExit(msg)
    return target_candidate


CONFIG_HEADER = "# Automatically generated by rust-build-release action"


def _write_target_config(path: Path, target: str, config: TargetConfig) -> None:
    """Create or update the target stanza inside `.cargo/config.toml`."""
    target_block = textwrap.dedent(
        f"""
        {CONFIG_HEADER}
        [target.{target}]
        linker = "{config.clang_triplet}-clang"
        ar = "llvm-ar"
        rustflags = ["-Clink-arg=-fuse-ld=lld"]
        """
    ).strip()

    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(CONFIG_HEADER)}\s*\n\[target\.{re.escape(target)}\].*?(?=^\s*\[|\Z)"
    )
    updated = pattern.sub("", existing)
    updated = updated.rstrip()
    if updated:
        updated = f"{updated}\n\n"

    path.write_text(f"{updated}{target_block}\n", encoding="utf-8")


@app.default
def main(
    *,
    target: typ.Annotated[
        str | None, Parameter(help="gnullvm target triple to configure.")
    ] = None,
) -> None:
    """Orchestrate the setup for a gnullvm build on a Windows runner."""
    if sys.platform != "win32":
        print("Not a Windows runner, skipping gnullvm setup.")
        return

    requested_target = _resolve_target(target)
    config = TARGET_CONFIGS[requested_target]

    print(f"Setting up for {requested_target} build on Windows...")

    llvm_mingw_version = _resolve_llvm_mingw_version()
    llvm_mingw_variant = _resolve_llvm_mingw_variant()
    zip_file = f"llvm-mingw-{llvm_mingw_version}-{llvm_mingw_variant}.zip"
    url = (
        "https://github.com/mstorsjo/llvm-mingw/releases/download/"
        f"{llvm_mingw_version}/{zip_file}"
    )
    expected_sha256 = _expected_archive_sha256(llvm_mingw_version, llvm_mingw_variant)
    if expected_sha256 is None:
        msg = (
            "No checksum is registered for llvm-mingw version "
            f"{llvm_mingw_version}; set RBR_LLVM_MINGW_SHA256 to proceed. "
            f"Expected download URL: {url}"
        )
        raise RuntimeError(msg)

    runner_temp = Path(os.environ["RUNNER_TEMP"])
    final_llvm_path = runner_temp / f"llvm-mingw-{llvm_mingw_variant}"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            extracted_path = download_and_unzip(
                url,
                temp_path,
                expected_sha256=expected_sha256,
            )
            if final_llvm_path.exists():
                shutil.rmtree(final_llvm_path)
            shutil.move(str(extracted_path), str(final_llvm_path))
    except Exception as exc:
        print(
            f"::error:: Failed to download or extract llvm-mingw: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    llvm_bin_path = final_llvm_path / "bin"
    add_to_path(str(llvm_bin_path))
    print(f"Added {llvm_bin_path} to PATH.")

    cargo_dir = Path.cwd() / ".cargo"
    cargo_dir.mkdir(parents=True, exist_ok=True)
    config_toml_path = cargo_dir / "config.toml"
    _write_target_config(config_toml_path, requested_target, config)
    print(f"Updated {config_toml_path} with linker configuration.")

    env_target = requested_target.replace("-", "_")
    env_vars = {
        "CROSS_NO_DOCKER": "1",
        f"CC_{env_target}": f"{config.clang_triplet}-clang",
        f"CXX_{env_target}": f"{config.clang_triplet}-clang++",
        f"AR_{env_target}": "llvm-ar",
        f"RANLIB_{env_target}": "llvm-ranlib",
    }
    for key, value in env_vars.items():
        set_env(key, value)

    print("Set environment variables for gnullvm target.")
    print("gnullvm setup complete.")


if __name__ == "__main__":
    app()
