"""Vocabulary the mutation preflight's phases share.

The statuses, the lane set and mutmut's copy set are stated once here, so no
phase can pass by measuring a different set of directories from the one the
mutation run will actually copy.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

#: A phase succeeded.
SUCCEEDED: typ.Final[str] = "pass"
#: A phase found something that must stop the run.
FAILED: typ.Final[str] = "fail"
#: A phase does not apply to this lane, or nothing was declared for it.
SKIPPED: typ.Final[str] = "skipped"
#: A phase could not decide, and says so rather than guessing.
UNKNOWN: typ.Final[str] = "unknown"

#: The lanes this helper knows. Only ``mutmut`` copies a filtered sandbox.
LANES: typ.Final[frozenset[str]] = frozenset({"mutmut", "cargo"})

#: The helper scripts the workflows run need this interpreter.
HELPER_PYTHON_FLOOR: typ.Final[str] = "3.13"

#: What each mutmut release materializes besides ``source_paths`` and
#: ``also_copy``, and the root-level globs it adds. Keyed by ``major.minor``,
#: because the copy contract belongs to the release: a preflight that assumed
#: one release's set while another ran would certify the wrong sandbox. Read
#: from ``mutmut/configuration.py`` at each tag; extend this table when the
#: workflow's supported range grows.
MUTMUT_COPY_CONTRACTS: typ.Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "3.6": (("tests", "test", "setup.cfg", "pyproject.toml"), ("test*.py",)),
    "3.7": (
        ("tests", "test", "setup.cfg", "pyproject.toml", "uv.lock"),
        ("test*.py",),
    ),
}

#: The mutmut release the workflow pins unless a caller overrides it.
MUTMUT_PINNED_VERSION: typ.Final[str] = "3.6.0"

#: Directories a mutation sandbox must never carry: they are build output, a
#: tool cache or version control, and copying one either wastes the run or
#: hides a requirement the declaration should have named.
EXCLUDED_DIRECTORIES: typ.Final[frozenset[str]] = frozenset(
    {
        ".cache",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".uv-cache",
        ".uv-tools",
        ".venv",
        ".venv-coverage",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "mutants",
        "node_modules",
        "target",
    }
)

#: The keys a declaration may carry. Anything else is a finding, so a typo
#: cannot read as a requirement that is being checked when it is not.
DECLARATION_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"sandbox_paths", "require_commands", "native", "checks"}
)

#: Keys one ``require_commands`` entry may carry.
COMMAND_KEYS: typ.Final[frozenset[str]] = frozenset({"name", "version", "version_args"})

#: Keys the ``native`` table may carry.
NATIVE_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"package", "revision", "supported_python", "probe"}
)

#: Keys one ``checks`` entry may carry.
CHECK_KEYS: typ.Final[frozenset[str]] = frozenset(
    {"name", "command", "cwd", "timeout_seconds", "env"}
)

#: Bytes of one stream kept for a check. Later bytes are dropped and counted,
#: so a noisy command cannot make the diagnostics unreadable.
LOG_BOUND_BYTES: typ.Final[int] = 4096

#: Ceiling on one declared check, so a hung command cannot outlive the job.
MAX_CHECK_TIMEOUT_SECONDS: typ.Final[int] = 900

#: Ceiling on asking an external command for its version.
VERSION_TIMEOUT_SECONDS: typ.Final[int] = 60

#: A dotted version token in a command's output, used for exact comparison.
#: A PEP 440 pre/post/dev suffix stays part of the token, so ``1.2.3rc1`` is
#: not read as the stable ``1.2.3`` it merely resembles. PEP 440 accepts a
#: dot, hyphen or underscore before that suffix and allows the numeral to be
#: absent, so ``1.2.3-rc1``, ``1.2.3_rc1`` and ``1.2.3-beta`` are separate
#: releases too. An attached suffix keeps its required digit, which is what
#: stops ``1.2.3build`` being read as the ``1.2.3b`` it never meant.
#:
#: The suffix repeats, because a development build of a pre-release is another
#: release again: ``1.2.3rc1.dev1`` must not report the ``1.2.3rc1`` it merely
#: begins with.
_VERSION_SUFFIX: typ.Final[str] = (
    r"(?:[._-](?:alpha|beta|a|b|rc|post|dev)\d*"
    r"|\.?(?:alpha|beta|a|b|rc|post|dev)\d+)"
)
VERSION_TOKEN: typ.Final[re.Pattern[str]] = re.compile(
    rf"\d+(?:\.\d+)+{_VERSION_SUFFIX}*",
    re.IGNORECASE,
)

#: The checkout this helper belongs to. The package sits one directory below
#: ``workflow_scripts/``, which sits one below the repository root.
REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]

#: The shared interpreter rule (#565), read from this checkout rather than
#: installed: the caller's environment holds the caller's dependencies.
SHARED_RULE: typ.Final[Path] = (
    REPOSITORY_ROOT
    / "packages"
    / "cv005-contracts"
    / "cv005_contracts"
    / "requires_python.py"
)
