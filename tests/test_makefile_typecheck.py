"""Behavioural tests for the Makefile typecheck target."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from plumbum import local

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
_TYPECHECK_FIRST_EXTRA_PATHS = (
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
)
_TYPECHECK_FIRST_SOURCE_PATHS = (
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
    ".github/actions/windows-package/scripts",
)
_TYPECHECK_SECOND_EXTRA_PATHS = (
    ".",
    ".github/actions/macos-package/scripts",
)
_TYPECHECK_SECOND_SOURCE_PATHS = (".github/actions/macos-package/scripts",)
_UV_TYPECHECK_PREFIX = ("run", "ty", "check")


def _extra_search_arguments(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Build Ty's ordered ``--extra-search-path`` command arguments."""
    return tuple(token for path in paths for token in ("--extra-search-path", path))


def test_typecheck_target_runs_both_ty_commands_through_uv(tmp_path: Path) -> None:
    """Record the two real target invocations without running Ty itself."""
    (tmp_path / ".venv").mkdir()
    uv_recorder = tmp_path / "uv_recorder.py"
    uv_recorder.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['UV_COMMAND_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    command_log = tmp_path / "uv-commands.jsonl"

    make = local["make"][
        "-f",
        str(MAKEFILE_PATH),
        "--no-print-directory",
        f"UV={shlex.join((Path(sys.executable).as_posix(), uv_recorder.as_posix()))}",
        "typecheck",
    ]
    make.with_cwd(tmp_path).with_env(UV_COMMAND_LOG=str(command_log))()

    invocations = [
        json.loads(line)
        for line in command_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(invocations) == 2, (
        "typecheck must invoke UV exactly twice, once for each configured source set"
    )
    assert invocations[0][:3] == list(_UV_TYPECHECK_PREFIX), (
        "the primary typecheck command must be uv run ty check"
    )
    assert invocations[0][3:] == [
        *_extra_search_arguments(_TYPECHECK_FIRST_EXTRA_PATHS),
        *_TYPECHECK_FIRST_SOURCE_PATHS,
    ], "the primary typecheck command must preserve all search and source paths"
    assert invocations[1][:3] == list(_UV_TYPECHECK_PREFIX), (
        "the macOS typecheck command must be uv run ty check"
    )
    assert invocations[1][3:] == [
        *_extra_search_arguments(_TYPECHECK_SECOND_EXTRA_PATHS),
        *_TYPECHECK_SECOND_SOURCE_PATHS,
    ], "the macOS typecheck command must preserve its search and source paths"
