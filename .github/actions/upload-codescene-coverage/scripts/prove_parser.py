#!/usr/bin/env python3
"""Prove the pinned CodeScene CLI parses each Slipcover fixture.

``test-codescene-parser-proof.yml`` runs this as its parse step's sole
command. For every fixture it runs ``cs-coverage check`` and fails when the
CLI reports the known 1.0.101 parser break, exits non-zero, or does not
report a passing gate. The judgement is a pure function of the exit status
and the output, so each verdict is tested without the service.

Living in a script rather than a ``run:`` block matters to the workflow's
contract: a shell block holding ``false && cs-coverage check ...`` still
contains the command's text, while a step whose sole command is this script
either runs it or does not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: The output line of the parser break cs-coverage 1.0.101 is known to have.
KNOWN_PARSER_FAILURE = (
    "No matching field found: close for class java.io.InputStreamReader"
)
#: The output line of a passing gate.
PASSING_GATE = "Code coverage gates: PASS"
#: Where the fixtures live, relative to the repository root.
FIXTURE_GLOB = (
    ".github/actions/upload-codescene-coverage/tests/fixtures/slipcover-*-cobertura.xml"
)


def verdict(status: int, output: str) -> str | None:
    """Return why one ``cs-coverage check`` run fails the proof, or ``None``.

    The known parser break fails the proof even when the CLI exits zero, so
    it is judged first.

    Examples
    --------
    >>> verdict(0, "Code coverage gates: PASS")
    >>> verdict(0, KNOWN_PARSER_FAILURE)
    'cs-coverage 1.0.101 reported the known parser failure'
    >>> verdict(2, "")
    'cs-coverage exited 2'
    """
    if KNOWN_PARSER_FAILURE in output:
        return "cs-coverage 1.0.101 reported the known parser failure"
    if status != 0:
        return f"cs-coverage exited {status}"
    if PASSING_GATE not in output:
        return "cs-coverage did not report a PASS result"
    return None


def _check(report: Path) -> tuple[int, str]:
    """Run ``cs-coverage check`` on one report; return status and output."""
    # Stdlib only, like install_cs_coverage.py beside it, so the workflow runs
    # it with the runner's python3 and no dependency install.
    completed = subprocess.run(  # noqa: S603, TID251 - fixed argv, no shell.
        ["cs-coverage", "check", "--coverage-files", str(report)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CS_DISABLE_VERSION_CHECK": "1"},
    )
    output = completed.stdout + completed.stderr
    sys.stdout.write(output)
    return completed.returncode, output


def main(root: Path) -> int:
    """Prove every fixture under *root*; return the process exit status."""
    if not os.environ.get("CS_ACCESS_TOKEN"):
        sys.stderr.write("CS_ACCESS_TOKEN is required for parser proof\n")
        return 1
    reports = sorted(root.glob(FIXTURE_GLOB))
    if not reports:
        sys.stderr.write(
            "no Slipcover cobertura fixtures remain for the parser proof\n"
        )
        return 1
    for report in reports:
        status, output = _check(report)
        reason = verdict(status, output)
        if reason is not None:
            sys.stderr.write(f"{report.name}: {reason}\n")
            return status or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path.cwd()))
