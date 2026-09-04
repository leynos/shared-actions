"""Resolve one manifest entry for a tool, version and runner.

Pure by construction: it reads the manifest and the runner description, prints
`key=value` lines on stdout, and performs no side effect of its own. Every
outcome, including every failure, is a `status` line rather than an exception,
so the calling step owns the decision about what to do with it and there is one
place where failure is turned into a job failure.

Exit status is 0 for every resolvable outcome and 2 only when the manifest
itself could not be read, because that is a defect in this repository rather
than a condition a caller can cause.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

#: Runner OS and architecture to Rust target triple. GitHub reports these as
#: `runner.os` and `runner.arch`; the pair is what decides which archive an
#: entry offers, so an unlisted pair is resolved as unsupported rather than
#: guessed at.
TARGETS = {
    ("Linux", "X64"): "x86_64-unknown-linux-gnu",
    ("Linux", "ARM64"): "aarch64-unknown-linux-gnu",
    ("macOS", "X64"): "x86_64-apple-darwin",
    ("macOS", "ARM64"): "aarch64-apple-darwin",
    ("Windows", "X64"): "x86_64-pc-windows-msvc",
}

#: Archive extensions this repository knows how to extract. The extractor is
#: chosen by extension and never by probing what `tar` resolves to, because Git
#: Bash puts MSYS GNU tar ahead of the system bsdtar that can read a zip.
EXTENSIONS = ("tar.gz", "tgz", "zip")


def emit(**fields: object) -> None:
    """Print one `key=value` line per field, in a stable order."""
    for key, value in fields.items():
        print(f"{key.replace('_', '-')}={value}")


def extension_of(url: str) -> str | None:
    """Return the archive extension named by a URL, or None if unknown."""
    name = url.rsplit("/", 1)[-1]
    for extension in EXTENSIONS:
        if name.endswith("." + extension):
            return extension
    return None


def resolve(
    manifest: dict, tool: str, version: str, runner_os: str, runner_arch: str
) -> dict[str, object]:
    """Return the fields describing one entry, or the reason there is none."""
    entries = [entry for entry in manifest.get("tool", []) if entry.get("name") == tool]
    if not entries:
        known = sorted({entry.get("name", "") for entry in manifest.get("tool", [])})
        return {
            "status": "error",
            "error_kind": "unknown-tool",
            "error_message": (
                f"{tool} is not in the manifest; it lists {', '.join(known)}"
            ),
        }

    matching = [entry for entry in entries if entry.get("version") == version]
    if not matching:
        available = sorted({str(entry.get("version")) for entry in entries})
        return {
            "status": "error",
            "error_kind": "unknown-version",
            "error_message": (
                f"{tool} {version} is not in the manifest; it lists "
                f"{', '.join(available)}. Add the version rather than floating to one."
            ),
        }
    entry = matching[0]

    triple = TARGETS.get((runner_os, runner_arch))
    if triple is None:
        return {
            "status": "error",
            "error_kind": "unsupported-runner",
            "error_message": f"unsupported runner {runner_os}/{runner_arch}",
        }

    targets = [
        target for target in entry.get("target", []) if target.get("triple") == triple
    ]
    if not targets:
        offered = sorted(
            {str(target.get("triple")) for target in entry.get("target", [])}
        )
        return {
            "status": "error",
            "error_kind": "unsupported-target",
            "error_message": (
                f"{tool} {version} publishes no archive for {triple}; "
                f"it offers {', '.join(offered)}"
            ),
        }
    target = targets[0]

    extension = extension_of(str(target.get("url", "")))
    if extension is None:
        return {
            "status": "error",
            "error_kind": "unsupported-extension",
            "error_message": (
                f"{tool} {version} {triple} names an archive this action cannot "
                f"extract; it handles {', '.join(EXTENSIONS)}"
            ),
        }

    binary = str(entry.get("binary", tool))
    version_args = entry.get("version-args", [])
    return {
        "status": "ok",
        "triple": triple,
        "url": target["url"],
        "sha256": target["sha256"],
        "member": target["member"],
        "sidecar": target.get("sidecar", "unchecked"),
        "extension": extension,
        "binary": binary + (".exe" if runner_os == "Windows" else ""),
        # Space-separated because a shell step reads this back, and no argument
        # any of these tools takes contains a space.
        "version_args": " ".join(str(argument) for argument in version_args),
        # A tool that cannot be asked its version is recorded as such rather
        # than silently skipped: dylint-link refuses every argument unless
        # RUSTUP_TOOLCHAIN is set, so there is nothing to read back.
        "version_check": "true" if version_args else "false",
        "expected_version": f"{binary} {version}",
    }


def main(argv: list[str] | None = None) -> int:
    """Resolve one entry and print it, returning the process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--runner-os", required=True)
    parser.add_argument("--runner-arch", required=True)
    arguments = parser.parse_args(argv)

    try:
        with arguments.manifest.open("rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        emit(
            status="error",
            error_kind="manifest-unreadable",
            error_message=f"could not read {arguments.manifest}: {error}",
        )
        return 2

    emit(
        **resolve(
            manifest,
            arguments.tool,
            arguments.version,
            arguments.runner_os,
            arguments.runner_arch,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
