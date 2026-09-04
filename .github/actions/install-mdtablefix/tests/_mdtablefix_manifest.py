"""Load the install-mdtablefix action manifest for the test suite.

The helpers here are read-only queries over ``action.yml``. They give the
contract tests and the scenario harness a single place that knows where the
manifest lives and how its steps are named.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

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
SUPPORTED_PLATFORMS = ("Linux:X64", "Linux:ARM64")

#: Runner pairs the action must reject rather than build from source.
UNSUPPORTED_PLATFORMS = ("macOS:ARM64", "macOS:X64", "Windows:X64", "Linux:ARM")


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


def step_by_name(name: str) -> dict[str, object]:
    """Return the single step declaring ``name``."""
    step = next((item for item in manifest_steps() if item.get("name") == name), None)
    if step is None:
        message = f"no step named {name!r} in {ACTION_PATH}"
        raise LookupError(message)
    return step


def step_script(name: str) -> str:
    """Return the Bash fragment a step declares."""
    script = step_by_name(name)["run"]
    if not isinstance(script, str):
        message = f"step {name!r} declares no Bash fragment"
        raise TypeError(message)
    return script


def step_env(name: str) -> dict[str, str]:
    """Return the environment mapping a step declares."""
    return typ.cast("dict[str, str]", step_by_name(name).get("env") or {})
