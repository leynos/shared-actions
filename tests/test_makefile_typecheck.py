"""Behavioural tests for the Makefile typecheck target."""

from __future__ import annotations

import shlex
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

#: The typecheck recipe is a POSIX `make` recipe, and the stub Ty is a bash
#: script. Neither runs on the Windows runner, which is why this module is
#: skipped there rather than left to fail the `python-tests-windows` job.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the typecheck target is a POSIX make recipe"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"

#: Root-level modules that must be type-checked. A module left out of the
#: target is not merely unchecked: nothing reports that it was skipped, so the
#: omission is invisible until a type error reaches a release.
ROOT_MODULES: typ.Final = (
    "action_pins.py",
    "cmd_utils.py",
    "composite_fragments.py",
)

#: Targets the typecheck recipe must hand to Ty, so that a reorganisation of
#: the file list cannot quietly drop a whole directory from checking.
CHECKED_PATHS: typ.Final = (
    ".github/actions/generate-coverage/scripts",
    ".github/actions/macos-package/scripts",
    ".github/actions/ratchet-coverage/scripts",
    ".github/actions/rust-build-release/src",
    ".github/actions/setup-rust/scripts",
    ".github/actions/windows-package/scripts",
)


def _typecheck_invocations(tmp_path: Path) -> list[list[str]]:
    """Run the typecheck target against a stub Ty and return its invocations.

    The stub records argv instead of checking anything, so the assertions are
    about the target's shape rather than about Ty's verdict.
    """
    ty_path = tmp_path / ".venv" / "bin" / "ty"
    ty_path.parent.mkdir(parents=True)
    ty_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$*" >> "${TY_COMMAND_LOG:?}"\n',
        encoding="utf-8",
    )
    ty_path.chmod(0o755)
    command_log = tmp_path / "ty-commands.log"

    make = local["make"][
        "-f",
        str(MAKEFILE_PATH),
        "--no-print-directory",
        "typecheck",
    ]
    make.with_cwd(tmp_path).with_env(TY_COMMAND_LOG=str(command_log))()

    return [
        shlex.split(line)
        for line in command_log.read_text(encoding="utf-8").splitlines()
    ]


def test_typecheck_target_passes_project_venv_to_every_ty_invocation(
    tmp_path: Path,
) -> None:
    """Each Ty call must resolve types through the project's own ``.venv``."""
    invocations = _typecheck_invocations(tmp_path)

    assert invocations, "the typecheck target ran no Ty invocation"
    for invocation in invocations:
        assert invocation[:3] == ["check", "--python", ".venv"], invocation


def test_typecheck_target_checks_every_root_module(tmp_path: Path) -> None:
    """A root module missing from the target is unchecked and unreported."""
    checked = {
        argument
        for invocation in _typecheck_invocations(tmp_path)
        for argument in invocation
    }

    missing = [module for module in ROOT_MODULES if module not in checked]

    assert not missing, f"root modules absent from the typecheck target: {missing}"


def test_typecheck_target_checks_each_expected_scripts_directory(
    tmp_path: Path,
) -> None:
    """Every directory named as a target must reach Ty, not just a search path.

    ``--extra-search-path`` resolves imports; it does not select anything for
    checking. A directory listed only as a search path looks present in the
    recipe while nothing in it is verified.
    """
    invocations = _typecheck_invocations(tmp_path)
    search_paths = {
        invocation[index + 1]
        for invocation in invocations
        for index, argument in enumerate(invocation)
        if argument == "--extra-search-path"
    }
    targets = [
        argument
        for invocation in invocations
        for argument in invocation
        if not argument.startswith("-")
    ]

    absent = [
        path
        for path in CHECKED_PATHS
        if path not in targets and path not in search_paths
    ]

    assert not absent, f"paths the typecheck target never mentions: {absent}"
