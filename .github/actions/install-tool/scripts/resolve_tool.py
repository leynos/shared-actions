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
import typing as typ
from pathlib import Path


class Runner(typ.NamedTuple):
    """The runner description that decides which archive an entry offers.

    A pair rather than two arguments, because neither half means anything
    without the other and every function taking them takes both.
    """

    os: str
    arch: str


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

#: Every reason resolution can fail. The calling step turns each into a
#: bounded `install-tool.resolve` metric, so the set is closed here and there.
UNKNOWN_TOOL = "unknown-tool"
UNKNOWN_VERSION = "unknown-version"
UNSUPPORTED_RUNNER = "unsupported-runner"
UNSUPPORTED_TARGET = "unsupported-target"
UNSUPPORTED_EXTENSION = "unsupported-extension"
MANIFEST_UNREADABLE = "manifest-unreadable"
UNSUPPORTED_SCHEMA = "unsupported-schema"

#: The manifest layout this resolver understands. A later one may move or
#: rename fields, and reading it with these rules would resolve something
#: plausible and wrong rather than failing.
SCHEMA = 1


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


class _UnresolvedError(Exception):
    """One reason an entry could not be resolved.

    Internal to this module and never allowed to escape it: `resolve` catches
    it and returns the fields, so callers still see every failure as data. It
    exists so each selection step can be read as the one question it answers,
    rather than as another branch in a function that answers five.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    def as_fields(self) -> dict[str, object]:
        """Return this failure in the shape `resolve` returns."""
        return {
            "status": "error",
            "error_kind": self.kind,
            "error_message": self.message,
        }


def select_tool(manifest: dict, tool: str) -> list[dict]:
    """Return every entry for a tool, whatever version each carries."""
    entries = [entry for entry in manifest.get("tool", []) if entry.get("name") == tool]
    if not entries:
        known = sorted({entry.get("name", "") for entry in manifest.get("tool", [])})
        message = f"{tool} is not in the manifest; it lists {', '.join(known)}"
        raise _UnresolvedError(UNKNOWN_TOOL, message)
    return entries


def select_version(entries: list[dict], tool: str, version: str) -> dict:
    """Return the entry at an exact version, naming the fix if there is none."""
    matching = [entry for entry in entries if entry.get("version") == version]
    if not matching:
        available = sorted({str(entry.get("version")) for entry in entries})
        message = (
            f"{tool} {version} is not in the manifest; it lists "
            f"{', '.join(available)}. Add the version rather than floating to one."
        )
        raise _UnresolvedError(UNKNOWN_VERSION, message)
    return matching[0]


def select_triple(runner: Runner) -> str:
    """Return the target triple for a runner, refusing to guess at an unknown."""
    triple = TARGETS.get((runner.os, runner.arch))
    if triple is None:
        message = f"unsupported runner {runner.os}/{runner.arch}"
        raise _UnresolvedError(UNSUPPORTED_RUNNER, message)
    return triple


def select_target(entry: dict, triple: str) -> dict:
    """Return the archive an entry offers for a triple."""
    targets = [
        target for target in entry.get("target", []) if target.get("triple") == triple
    ]
    if not targets:
        offered = sorted(
            {str(target.get("triple")) for target in entry.get("target", [])}
        )
        message = (
            f"{entry['name']} {entry['version']} publishes no archive for "
            f"{triple}; it offers {', '.join(offered)}"
        )
        raise _UnresolvedError(UNSUPPORTED_TARGET, message)
    return targets[0]


def select_extension(entry: dict, triple: str, url: str) -> str:
    """Return the archive extension, refusing one this action cannot extract."""
    extension = extension_of(url)
    if extension is None:
        message = (
            f"{entry['name']} {entry['version']} {triple} names an archive this "
            f"action cannot extract; it handles {', '.join(EXTENSIONS)}"
        )
        raise _UnresolvedError(UNSUPPORTED_EXTENSION, message)
    return extension


def describe(
    entry: dict, target: dict, extension: str, runner: Runner
) -> dict[str, object]:
    """Return the fields a resolved entry publishes to the calling step."""
    binary = str(entry.get("binary", entry["name"]))
    version_args = entry.get("version-args", [])
    return {
        "status": "ok",
        "triple": target["triple"],
        "url": target["url"],
        "sha256": target["sha256"],
        "member": target["member"],
        "sidecar_verified": target.get("sidecar-verified", "false"),
        "extension": extension,
        "binary": binary + (".exe" if runner.os == "Windows" else ""),
        # Space-separated because a shell step reads this back, and no argument
        # any of these tools takes contains a space.
        "version_args": " ".join(str(argument) for argument in version_args),
        # A tool that cannot be asked its version is recorded as such rather
        # than silently skipped: dylint-link refuses every argument unless
        # RUSTUP_TOOLCHAIN is set, so there is nothing to read back.
        "version_check": "true" if version_args else "false",
        "expected_version": f"{binary} {entry['version']}",
    }


def resolve(
    manifest: dict, tool: str, version: str, runner: Runner
) -> dict[str, object]:
    """Return the fields describing one entry, or the reason there is none."""
    try:
        entry = select_version(select_tool(manifest, tool), tool, version)
        triple = select_triple(runner)
        target = select_target(entry, triple)
        extension = select_extension(entry, triple, str(target.get("url", "")))
    except _UnresolvedError as unresolved:
        return unresolved.as_fields()
    return describe(entry, target, extension, runner)


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
            error_kind=MANIFEST_UNREADABLE,
            error_message=f"could not read {arguments.manifest}: {error}",
        )
        return 2

    schema = manifest.get("schema")
    if schema != SCHEMA:
        emit(
            status="error",
            error_kind=UNSUPPORTED_SCHEMA,
            error_message=(
                f"{arguments.manifest} declares schema {schema!r}; this action "
                f"reads schema {SCHEMA}"
            ),
        )
        return 0

    runner = Runner(arguments.runner_os, arguments.runner_arch)
    emit(**resolve(manifest, arguments.tool, arguments.version, runner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
