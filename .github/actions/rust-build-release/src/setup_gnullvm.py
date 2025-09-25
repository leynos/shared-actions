#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.9"
# dependencies = ["cyclopts>=2.9,<4"]
# ///
"""Set up the environment for building with the x86_64-pc-windows-gnullvm target."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

from cyclopts import App

LLVM_MINGW_DEFAULT_VERSION = "20250924"
LLVM_MINGW_VARIANT = "ucrt-x86_64"
KNOWN_LLVM_MINGW_SHA256 = {
    "20250924": {
        LLVM_MINGW_VARIANT: (
            "d2719495e711f5d07cb0781be5eb987ba549b07075578e34387dd127eb1341e8"
        ),
    }
}

TARGET = "x86_64-pc-windows-gnullvm"

app = App()


def _resolve_llvm_mingw_version() -> str:
    """Return the llvm-mingw release version to install."""
    return os.environ.get("RBR_LLVM_MINGW_VERSION", LLVM_MINGW_DEFAULT_VERSION)


def _expected_archive_sha256(version: str) -> str | None:
    """Return the expected SHA-256 for the archive of *version*."""
    if override := os.environ.get("RBR_LLVM_MINGW_SHA256"):
        return override
    return KNOWN_LLVM_MINGW_SHA256.get(version, {}).get(LLVM_MINGW_VARIANT)


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
                error_msg = "Failed to download llvm-mingw archive"
                raise RuntimeError(error_msg) from exc
            sleep_seconds = 2**attempt
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
            f"Expected {expected_sha256}, got {actual_sha256}."
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
            try:
                target_path.relative_to(base)
            except ValueError as exc:
                msg = f"Unsafe path in archive: {member.filename}"
                raise RuntimeError(msg) from exc
        zip_ref.extractall(base)

    extracted_dirs = [path for path in dest.iterdir() if path.is_dir()]
    if not extracted_dirs:
        msg = f"Zip file from {url} did not contain a directory."
        raise RuntimeError(msg)

    return extracted_dirs[0]


@app.default
def main() -> None:
    """Orchestrate the setup for a gnullvm build on a Windows runner."""
    if sys.platform != "win32":
        print("Not a Windows runner, skipping gnullvm setup.")
        return

    print(f"Setting up for {TARGET} build on Windows...")

    llvm_mingw_version = _resolve_llvm_mingw_version()
    expected_sha256 = _expected_archive_sha256(llvm_mingw_version)
    if expected_sha256 is None:
        msg = (
            "No checksum is registered for llvm-mingw version "
            f"{llvm_mingw_version}; set RBR_LLVM_MINGW_SHA256 to proceed."
        )
        raise RuntimeError(msg)

    runner_temp = Path(os.environ["RUNNER_TEMP"])
    final_llvm_path = runner_temp / "llvm-mingw-ucrt"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            zip_file = f"llvm-mingw-{llvm_mingw_version}-{LLVM_MINGW_VARIANT}.zip"
            url = (
                "https://github.com/mstorsjo/llvm-mingw/releases/download/"
                f"{llvm_mingw_version}/{zip_file}"
            )
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
            f"::error::Failed to download or extract llvm-mingw: {exc}", file=sys.stderr
        )
        raise SystemExit(1) from exc

    llvm_bin_path = final_llvm_path / "bin"
    add_to_path(str(llvm_bin_path))
    print(f"Added {llvm_bin_path} to PATH.")

    project_root_env = os.environ.get("GITHUB_WORKSPACE")
    project_root = Path(project_root_env) if project_root_env else Path.cwd()
    cargo_dir = project_root / ".cargo"
    cargo_dir.mkdir(exist_ok=True)
    config_toml_path = cargo_dir / "config.toml"
    config_content = f"""
# Automatically generated by rust-build-release action
[target.{TARGET}]
linker = "x86_64-w64-mingw32-clang"
ar = "llvm-ar"
rustflags = ["-Clink-arg=-fuse-ld=lld"]
"""
    config_toml_path.write_text(config_content.strip(), encoding="utf-8")
    print(f"Created {config_toml_path} with linker configuration.")

    set_env("CROSS_NO_DOCKER", "1")
    env_target = TARGET.replace("-", "_")
    env_vars = {
        f"CC_{env_target}": "x86_64-w64-mingw32-clang",
        f"CXX_{env_target}": "x86_64-w64-mingw32-clang++",
        f"AR_{env_target}": "llvm-ar",
        f"RANLIB_{env_target}": "llvm-ranlib",
        f"CFLAGS_{env_target}": "--target=x86_64-w64-windows-gnu",
        f"CXXFLAGS_{env_target}": "--target=x86_64-w64-windows-gnu",
    }
    for key, value in env_vars.items():
        set_env(key, value)

    print("Set environment variables for gnullvm target.")
    print("gnullvm setup complete.")


if __name__ == "__main__":
    app()
