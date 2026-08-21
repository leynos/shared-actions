"""Contract tests for the Skylos Makefile integration."""

from __future__ import annotations

import shlex
from pathlib import Path

from plumbum import local

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _run_make(*args: str) -> tuple[int, str, str]:
    """Run Make in the repository root and capture its output."""
    return local["make"]["--no-print-directory", *args].run(
        retcode=None, cwd=_REPOSITORY_ROOT
    )


def test_skylos_allow_uses_the_standalone_whitelist_subcommand() -> None:
    """The whitelist command must not inherit scan-only options."""
    returncode, stdout, stderr = _run_make(
        "--dry-run", "skylos-allow", "NAME=registered_handler"
    )

    assert returncode == 0, stderr
    commands = [
        shlex.split(line) for line in stdout.splitlines() if " skylos " in f" {line} "
    ]
    assert len(commands) == 1
    assert commands[0][-3:] == ["skylos", "whitelist", "${SKYLOS_NAME}"]
    assert "--config-file" not in commands[0]
    assert "--reason" not in commands[0]


def test_skylos_allow_requires_a_name() -> None:
    """The allow-list helper rejects an empty symbol name."""
    returncode, _stdout, stderr = _run_make("skylos-allow")

    assert returncode == 2
    assert "NAME is required for a named Skylos false positive" in stderr
