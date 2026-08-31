"""Behavioural tests for the Makefile typecheck target."""

from __future__ import annotations

import shlex
from pathlib import Path

from plumbum import local

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"


def test_typecheck_target_passes_project_venv_to_both_ty_invocations(
    tmp_path: Path,
) -> None:
    """Run the target and require both Ty calls to resolve through ``.venv``."""
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

    invocations = [
        shlex.split(line)
        for line in command_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(invocations) == 2
    for invocation in invocations:
        assert invocation[:3] == ["check", "--python", ".venv"]
    assert invocations[0][3:] == [
        "--extra-search-path",
        ".",
        "--extra-search-path",
        ".github/actions/generate-coverage/scripts",
        "--extra-search-path",
        ".github/actions/ratchet-coverage/scripts",
        "--extra-search-path",
        ".github/actions/rust-build-release",
        "--extra-search-path",
        ".github/actions/rust-build-release/src",
        "--extra-search-path",
        ".github/actions/linux-packages",
        "--extra-search-path",
        ".github/actions/linux-packages/scripts",
        "--extra-search-path",
        ".github/actions/windows-package",
        "--extra-search-path",
        ".github/actions/windows-package/scripts",
        "--extra-search-path",
        ".github/actions/setup-rust/scripts",
        "cmd_utils.py",
        ".github/actions/generate-coverage/scripts",
        ".github/actions/ratchet-coverage/scripts",
        ".github/actions/linux-packages/scripts",
        ".github/actions/rust-build-release/src",
        ".github/actions/setup-rust/scripts",
        ".github/actions/windows-package/scripts",
    ]
    assert invocations[1][3:] == [
        "--extra-search-path",
        ".",
        "--extra-search-path",
        ".github/actions/macos-package/scripts",
        ".github/actions/macos-package/scripts",
    ]
