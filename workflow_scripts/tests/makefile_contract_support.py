"""Facts and inspectors shared by the Makefile toolchain contract suites.

The Makefile and the CI workflows state the same things twice: once in the
file that runs, and once in a contract that reads it. These helpers read
the first so the suites can assert the second. Makeutil returns the
Makefile as structured facts, so the contracts compare recipes without
coupling to source spacing, and the workflow helpers return parsed YAML
rather than text.

Validation is written as ``raise`` rather than ``assert``: the repository
exempts only ``test_*.py`` files from ``S101``, and this is a support
module. The messages are the same either way, and the failure still
propagates as an ``AssertionError`` so a contract reads the same from
either side.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import typing as typ
from pathlib import Path

import yaml
from plumbum import local

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_REVISION: typ.Final = "7ab9b7b73a8f50ef1d3df548c7def7b94db35ba2"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_GNU_MAKE_INSTALL_TOKENS: typ.Final = (
    "choco",
    "install",
    "make",
    "--no-progress",
    "--yes",
)
_MAKEUTIL_ACTION_REFERENCE: typ.Final = "./.github/actions/install-makeutil-from-source"
_MAKEUTIL_ACTION_INPUTS: typ.Final = {
    "makeutil-revision": "${{ env.MAKEUTIL_REVISION }}",
    "makeutil-toolchain": "${{ env.MAKEUTIL_TOOLCHAIN }}",
}
_MAKEUTIL_ACTION_ENVIRONMENT: typ.Final = {
    "MAKEUTIL_REVISION": "${{ inputs.makeutil-revision }}",
    "MAKEUTIL_TOOLCHAIN": "${{ inputs.makeutil-toolchain }}",
}
_TYPECHECK_COMMAND_PREFIX: typ.Final = (
    "$(UV)",
    "run",
    "ty",
    "check",
    "--python",
    ".venv",
)
_TYPECHECK_FIRST_EXTRA_PATHS: typ.Final = (
    ".",
    ".github/actions/generate-coverage/scripts",
    ".github/actions/ratchet-coverage/scripts",
    ".github/actions/rust-build-release",
    ".github/actions/rust-build-release/src",
    ".github/actions/linux-packages",
    ".github/actions/linux-packages/scripts",
    ".github/actions/windows-package",
    ".github/actions/windows-package/scripts",
    ".github/actions/setup-rust/scripts",
    ".github/actions/install-mdtablefix/tests",
    ".github/actions/install-makeutil/scripts",
    ".github/actions/install-makeutil/tests",
    "uv_gate",
    "uv_gate/tests",
)
_TYPECHECK_FIRST_SOURCE_PATHS: typ.Final = (
    "cmd_utils.py",
    "composite_fragments.py",
    ".github/actions/generate-coverage/scripts",
    ".github/actions/ratchet-coverage/scripts",
    ".github/actions/linux-packages/scripts",
    ".github/actions/rust-build-release/src",
    ".github/actions/setup-rust/scripts",
    ".github/actions/install-mdtablefix/tests",
    ".github/actions/install-makeutil/scripts",
    ".github/actions/install-makeutil/tests",
    "uv_gate",
    ".github/actions/windows-package/scripts",
)
_TYPECHECK_SECOND_EXTRA_PATHS: typ.Final = (
    ".",
    ".github/actions/macos-package/scripts",
)
_TYPECHECK_SECOND_SOURCE_PATHS: typ.Final = (".github/actions/macos-package/scripts",)
_TYPECHECK_FIRST_EXTRA_PATH_TOKENS: typ.Final = tuple(
    token
    for path in _TYPECHECK_FIRST_EXTRA_PATHS
    for token in ("--extra-search-path", path)
)
_TYPECHECK_SECOND_EXTRA_PATH_TOKENS: typ.Final = tuple(
    token
    for path in _TYPECHECK_SECOND_EXTRA_PATHS
    for token in ("--extra-search-path", path)
)
_TYPECHECK_FIRST_RECIPE_TOKENS: typ.Final = (
    _TYPECHECK_COMMAND_PREFIX
    + _TYPECHECK_FIRST_EXTRA_PATH_TOKENS
    + _TYPECHECK_FIRST_SOURCE_PATHS
)
_TYPECHECK_SECOND_RECIPE_TOKENS: typ.Final = (
    _TYPECHECK_COMMAND_PREFIX
    + _TYPECHECK_SECOND_EXTRA_PATH_TOKENS
    + _TYPECHECK_SECOND_SOURCE_PATHS
)
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)


def _fail(message: str) -> typ.NoReturn:
    """Raise the contract failure every inspector shares."""
    raise AssertionError(message)


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object while naming an unexpected subject."""
    if not isinstance(value, dict):
        _fail(f"expected {subject} to be a JSON object")
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object array while naming an unexpected subject."""
    if not isinstance(value, list):
        _fail(f"expected {subject} to be a JSON array")
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array while naming an unexpected subject."""
    if not isinstance(value, list):
        _fail(f"expected {subject} to be a JSON array")
    if not all(isinstance(item, str) for item in value):
        _fail(f"expected {subject} to contain only JSON strings")
    return tuple(typ.cast("list[str]", value))


def _normalize_make_text(value: str) -> str:
    """Normalize Makeutil text before removing Make continuations."""
    return value.replace("\r\n", "\n").replace("\\\n", "")


def _makeutil_report() -> dict[str, object]:
    """Return Makeutil's complete Makefile parse report without caching it."""
    executable = shutil.which("makeutil")
    if executable is None:
        _fail("Makefile contract tests require makeutil on PATH")
    returncode, stdout, stderr = local[executable]["parse", "Makefile"].run(
        retcode=None, cwd=_REPOSITORY_ROOT
    )
    if returncode != 0:
        _fail(f"makeutil must parse Makefile successfully: {stderr}")
    report = _mapping(json.loads(stdout), subject="makeutil report")
    parse = _mapping(report.get("parse"), subject="makeutil parse report")
    if parse.get("status") != "complete":
        _fail(f"makeutil must complete the Makefile parse: {parse!r}")
    return report


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only Makeutil rule for ``target`` that has recipes."""
    rules = _objects(_makeutil_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    if len(matches) != 1:
        _fail(
            f"expected one recipe-bearing Makefile rule named {target!r}, found "
            f"{len(matches)}"
        )
    return matches[0]


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens from every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(_normalize_make_text(recipe_text)))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _make_command(
    *arguments: str,
    environment: dict[str, str],
    working_directory: Path = _REPOSITORY_ROOT,
) -> tuple[int, str, str]:
    """Run the resolved Make executable with an inherited environment."""
    executable = shutil.which("make")
    if executable is None:
        _fail("Makefile contract tests require make on PATH")
    with local.env(**environment):
        return local[executable]["--no-print-directory", *arguments].run(
            retcode=None, cwd=working_directory
        )


def _clean_environment(**values: str) -> dict[str, str]:
    """Return the inherited environment, including WSL's ``NAME``, plus ``values``."""
    environment = dict(os.environ, NAME="wsl-hostname")
    environment.update(values)
    return environment


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return a named workflow job."""
    workflow = yaml.safe_load((_REPOSITORY_ROOT / workflow_path).read_text())
    jobs = _mapping(
        _mapping(workflow, subject=f"{workflow_path} workflow").get("jobs"),
        subject=f"{workflow_path} jobs",
    )
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} {job_name} job")


def _makeutil_action() -> dict[str, object]:
    """Return the local Makeutil composite-action manifest."""
    return _mapping(
        yaml.safe_load(
            (
                _REPOSITORY_ROOT
                / ".github/actions/install-makeutil-from-source/action.yml"
            ).read_text()
        ),
        subject="Makeutil composite action",
    )


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named workflow step for a job."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} {job_name} steps",
    )
    matches = [step for step in steps if step.get("name") == step_name]
    if len(matches) != 1:
        _fail(
            f"expected one {step_name!r} step in {workflow_path} {job_name!r}, found "
            f"{len(matches)}"
        )
    return matches[0]


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that a workflow command installs the pinned Makeutil parser."""
    if not isinstance(command, str):
        _fail(f"{contract} must provide a shell command")
    if tuple(shlex.split(_normalize_make_text(command))) != _MAKEUTIL_INSTALL_TOKENS:
        _fail(f"{contract} must install the pinned Makeutil revision and toolchain")


def _assert_gnu_make_installation(command: object, *, contract: str) -> None:
    """Assert that a Windows workflow command provisions GNU Make."""
    if not isinstance(command, str):
        _fail(f"{contract} must provide a shell command")
    if tuple(shlex.split(command)) != _GNU_MAKE_INSTALL_TOKENS:
        _fail(f"{contract} must install GNU Make through Chocolatey")
