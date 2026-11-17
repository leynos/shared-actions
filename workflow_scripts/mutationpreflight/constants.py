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

#: What mutmut 3.6.0 materializes besides ``source_paths`` and ``also_copy``.
#: Read from ``mutmut/configuration.py`` at the tag the workflow pins; bump
#: this list together with ``mutmut-version``.
MUTMUT_DEFAULT_COPIES: typ.Final[tuple[str, ...]] = (
    "tests",
    "test",
    "setup.cfg",
    "pyproject.toml",
)

#: Root-level globs mutmut 3.6.0 adds to its default copy set.
MUTMUT_DEFAULT_GLOBS: typ.Final[tuple[str, ...]] = ("test*.py",)

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
#: not read as the stable ``1.2.3`` it merely resembles. The suffix needs at
#: least one digit: without that, ``1.2.3build`` would be read as ``1.2.3b``,
#: a different release from the stable version it prints. The spelled-out
#: ``alpha`` and ``beta`` come first so a tool that prints them in full is not
#: read as the stable release their first letter resembles.
VERSION_TOKEN: typ.Final[re.Pattern[str]] = re.compile(
    r"\d+(?:\.\d+)+(?:\.?(?:alpha|beta|a|b|rc|post|dev)\d+)?", re.IGNORECASE
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
