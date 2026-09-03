"""Load the install-whitaker action manifest for the test suite.

The helpers here are read-only queries over ``action.yml``. They give the
contract tests and the fragment runner a single place that knows where the
manifest lives and how its steps are named.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"
DIGEST_MANIFEST_PATH = ACTION_DIR / "installer-digests.sha256"
RESOLVE_SCRIPT_PATH = ACTION_DIR / "scripts" / "resolve-release.sh"
DIGEST_MANIFEST_NAME = DIGEST_MANIFEST_PATH.name

#: Lifecycle step names, in the order the action runs them.
LIFECYCLE_STEP_NAMES = (
    "Report Whitaker installer cache",
    "Resolve Whitaker release",
    "Publish Whitaker resolution",
    "Download Whitaker release",
    "Verify Whitaker release",
    "Extract Whitaker installer",
    "Install Whitaker installer",
    "Run Whitaker installer",
)

#: Every runner pair the action supports, with the release asset it selects.
SUPPORTED_PLATFORMS = {
    "Linux:X64": ("x86_64-unknown-linux-gnu", "tgz", "whitaker-installer"),
    "Linux:ARM64": ("aarch64-unknown-linux-gnu", "tgz", "whitaker-installer"),
    "macOS:X64": ("x86_64-apple-darwin", "tgz", "whitaker-installer"),
    "macOS:ARM64": ("aarch64-apple-darwin", "tgz", "whitaker-installer"),
    "Windows:X64": ("x86_64-pc-windows-msvc", "zip", "whitaker-installer.exe"),
}


def load_manifest() -> dict[str, object]:
    """Load the action manifest."""
    return typ.cast(
        "dict[str, object]",
        yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8")),
    )


def manifest_steps() -> list[dict[str, object]]:
    """Return the composite steps in declaration order."""
    runs = typ.cast("dict[str, object]", load_manifest()["runs"])
    return typ.cast("list[dict[str, object]]", runs["steps"])


def _only_step(field: str, value: str) -> dict[str, object]:
    """Return the single step whose ``field`` equals ``value``."""
    step = next((item for item in manifest_steps() if item.get(field) == value), None)
    if step is None:
        message = f"no step with {field} {value!r} in {ACTION_PATH}"
        raise LookupError(message)
    return step


def step_by_name(name: str) -> dict[str, object]:
    """Return the single step declaring ``name``."""
    return _only_step("name", name)


def step_by_id(identifier: str) -> dict[str, object]:
    """Return the single step declaring ``identifier``."""
    return _only_step("id", identifier)


def lifecycle_steps() -> list[dict[str, object]]:
    """Return the run-bearing lifecycle steps in execution order."""
    return [step_by_name(name) for name in LIFECYCLE_STEP_NAMES]


#: Layout assumed for an unsupported pair, so a fixture can still be built.
_FALLBACK_PLATFORM = SUPPORTED_PLATFORMS["Linux:X64"]


def asset_name(runner_os: str, runner_arch: str, version: str) -> str:
    """Return the release asset the action selects for a runner pair.

    An unsupported pair falls back to the Linux x86_64 layout so a fixture can
    be built for it; the action rejects such a pair before it downloads
    anything.
    """
    target, extension, _ = SUPPORTED_PLATFORMS.get(
        f"{runner_os}:{runner_arch}",
        _FALLBACK_PLATFORM,
    )
    return f"whitaker-installer-{target}-v{version}.{extension}"


def installer_filename(runner_os: str, runner_arch: str) -> str:
    """Return the installer filename the action installs for a runner pair.

    An unsupported pair falls back to the POSIX filename so a fixture can be
    built for it; the action rejects such a pair before it extracts anything.
    """
    platform = SUPPORTED_PLATFORMS.get(
        f"{runner_os}:{runner_arch}",
        _FALLBACK_PLATFORM,
    )
    return platform[2]
