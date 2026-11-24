"""Writing the evidence a run publishes: its logs, its summary and its report.

A write that cannot happen is returned rather than raised, so the finding names
it and the phase's verdict can fail: evidence nobody can read must not be
reported as a passing run.
"""

from __future__ import annotations

import json
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from .models import Ran


class EvidenceFiles:
    """Writes the evidence a run publishes, and says when it cannot."""

    def write_check_logs(
        self, directory: Path, stem: str, name: str, outcome: Ran
    ) -> str | None:
        """Write one check's bounded streams, returning why they were not written."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{stem}.stdout.log").write_text(
                outcome.stdout, encoding="utf-8"
            )
            (directory / f"{stem}.stderr.log").write_text(
                outcome.stderr, encoding="utf-8"
            )
        except OSError as error:
            return f"check {name!r}'s logs could not be written: {error}"
        return None

    def append_summary(self, path: Path, rendered: str) -> str | None:
        """Append the rendered summary, returning why it could not be written."""
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(rendered)
        except OSError as error:
            return f"the job summary {str(path)!r} could not be written: {error}"
        return None

    def write_report(self, output_dir: Path, document: dict[str, object]) -> str | None:
        """Write the machine-readable report, returning why it was not written."""
        destination = output_dir / "mutation-preflight.json"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            return f"the report {str(destination)!r} could not be written: {error}"
        return None
