"""Load the install-mdtablefix action manifest for the test suite.

The helpers here are read-only queries over ``action.yml``. They give the
contract tests and the scenario harness a single place that knows where the
manifest lives and how its steps are named.
"""

from __future__ import annotations

import functools
import typing as typ
from pathlib import Path

import yaml

if typ.TYPE_CHECKING:
    from composite_fragments import CompositeStep

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"

#: Composite step names, in the order the action declares them.
STEP_NAMES = (
    "Validate mdtablefix inputs",
    "Check mdtablefix platform support",
    "Probe mdtablefix and cargo-binstall",
    "Install cargo-binstall",
    "Report cargo-binstall provisioning failure",
    "Install mdtablefix",
    "Verify mdtablefix",
)

#: The step that delegates to the upstream cargo-binstall installer.
BINSTALL_STEP_NAME = "Install cargo-binstall"

#: The step that annotates a failure of that upstream installer.
BINSTALL_FAILURE_STEP_NAME = "Report cargo-binstall provisioning failure"

#: The pinned upstream reference, commit SHA and all.
BINSTALL_ACTION_REF = (
    "cargo-bins/cargo-binstall@75b4bfae1b2c753a6806bbce6e6cb89b602de33c"
)

#: The cargo-binstall release the pinned SHA tags.
BINSTALL_ACTION_VERSION = "1.22.0"

#: The CLI override that neutralizes mdtablefix 0.5.0's ``bin-dir = "."``.
BIN_DIR_OVERRIDE = "{ bin }{ binary-ext }"

#: Every runner pair for which mdtablefix publishes a prebuilt archive.
SUPPORTED_PLATFORMS = (
    "Linux:X64",
    "Linux:ARM64",
    "macOS:X64",
    "macOS:ARM64",
    "Windows:X64",
)

#: Runner pairs the action must reject rather than build from source.
#:
#: `Windows:ARM64` is the interesting one: 0.5.1 publishes
#: `x86_64-pc-windows-msvc` and no aarch64 Windows archive, so a
#: `windows-11-arm` runner must still fail closed. `Linux:ARM` is 32-bit and
#: has no archive either.
UNSUPPORTED_PLATFORMS = ("Windows:ARM64", "Linux:ARM")


@functools.cache
def load_manifest() -> dict[str, object]:
    """Return the parsed action manifest.

    Parsed once per session and shared, because the whole suite reads the same
    file dozens of times and none of it writes to the manifest. Treat the
    returned document as read-only.
    """
    return typ.cast(
        "dict[str, object]",
        yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8")),
    )


def manifest_steps() -> list[CompositeStep]:
    """Return the composite steps in declaration order.

    This is where untyped YAML becomes a typed step, so it is where the cast
    belongs; every reader downstream then has a contract rather than a mapping
    of unknowns.
    """
    runs = typ.cast("dict[str, object]", load_manifest()["runs"])
    return typ.cast("list[CompositeStep]", runs["steps"])


def step_by_name(name: str) -> CompositeStep:
    """Return the single step declaring ``name``."""
    step = next((item for item in manifest_steps() if item.get("name") == name), None)
    if step is None:
        message = f"no step named {name!r} in {ACTION_PATH}"
        raise LookupError(message)
    return step


def step_script(name: str) -> str:
    """Return the Bash fragment a step declares."""
    script = step_by_name(name).get("run")
    if script is None:
        message = f"step {name!r} declares no Bash fragment"
        raise TypeError(message)
    return script


def step_env(name: str) -> dict[str, str]:
    """Return the environment mapping a step declares."""
    return step_by_name(name).get("env") or {}
