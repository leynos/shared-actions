"""The `cv005-contracts` command line.

`check` exits 0 when every selected contract holds, 1 when any clause is
broken (one line per violation), and 2 when the tree or the configuration
cannot be read. A reading failure is never reported as a pass.
"""

from __future__ import annotations

import sys
import typing as typ
from pathlib import Path

import cyclopts

from .api import FAMILIES, violations
from .config import ConfigError, load_config
from .loading import WorkflowReadingError

app = cyclopts.App(
    name="cv005-contracts",
    help="Check a repository's workflows against the CV-005 contracts.",
)

#: Exit statuses, so the Makefile and CI can tell a violation from a fault.
EXIT_CLEAN: typ.Final[int] = 0
EXIT_VIOLATIONS: typ.Final[int] = 1
EXIT_UNREADABLE: typ.Final[int] = 2


@app.command
def check(
    *,
    repository: Path = Path(),
    only: tuple[str, ...] = (),
) -> int:
    """Check a repository's workflows and print every violation.

    Parameters
    ----------
    repository : Path
        The repository root; the current directory by default.
    only : tuple[str, ...]
        Contract families to run, from pull-request, publisher, token,
        coverage and environment; every family when omitted.

    Returns
    -------
    int
        0 when clean, 1 on violations, 2 when nothing could be read.

    """
    try:
        config = load_config(repository)
        found = violations(repository, config, frozenset(only) or FAMILIES)
    except (ConfigError, WorkflowReadingError, ValueError) as error:
        print(f"cv005-contracts: {error}", file=sys.stderr)  # noqa: T201 - the CLI's report channel.
        return EXIT_UNREADABLE
    for item in found:
        print(item)  # noqa: T201 - the CLI's report channel.
    return EXIT_VIOLATIONS if found else EXIT_CLEAN


def main() -> None:
    """Run the command line and exit with its status."""
    status = app()
    sys.exit(status if isinstance(status, int) else EXIT_CLEAN)
