"""Tests for mutation-command output and exit-status capture."""

from __future__ import annotations

import json
import sys
import typing as typ

from workflow_scripts import mutation_capture

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestCaptureCommand:
    """Mutation command output and its exit status are retained together."""

    def test_failure_retains_both_streams_and_original_exit_status(
        self, tmp_path: Path
    ) -> None:
        """A baseline failure stays failed and keeps its first diagnostics."""
        output_dir = tmp_path / "diagnostics"
        script = (
            "import sys; print('baseline stdout'); "
            "print('first actionable baseline error', file=sys.stderr); "
            "raise SystemExit(23)"
        )
        command = [sys.executable, "-c", script]

        status = mutation_capture.capture_command(command, output_dir)

        assert status == 23, "the wrapper must preserve mutmut's failure code"
        assert (output_dir / "stdout.log").read_text() == "baseline stdout\n", (
            "Expected failure retains both streams and original exit status."
        )
        assert (
            output_dir / "stderr.log"
        ).read_text() == "first actionable baseline error\n", (
            "Expected failure retains both streams and original exit status."
        )
        assert not (tmp_path / "mutation-mutmut-results.txt").exists(), (
            "Expected failure retains both streams and original exit status."
        )
        assert not (tmp_path / "mutants" / "mutmut-stats.json").exists(), (
            "Expected failure retains both streams and original exit status."
        )
        metadata = json.loads((output_dir / "metadata.json").read_text())
        assert metadata["state"] == "complete", (
            "Expected failure retains both streams and original exit status."
        )
        assert metadata["exit_code"] == 23, (
            "Expected failure retains both streams and original exit status."
        )
        assert mutation_capture.verify_failure_capture(output_dir) == 0, (
            "Expected failure retains both streams and original exit status."
        )

    def test_capture_keeps_prefix_and_discards_output_past_per_stream_limit(
        self, tmp_path: Path
    ) -> None:
        """Each stream retains its first bytes and records tail truncation."""
        output_dir = tmp_path / "diagnostics"
        stdout = "first stdout diagnostic\n" + "o" * 80
        stderr = "first actionable stderr diagnostic\n" + "e" * 80
        script = (
            "import os; "
            f"os.write(1, {stdout.encode()!r}); "
            f"os.write(2, {stderr.encode()!r})"
        )
        command = [sys.executable, "-c", script]

        status = mutation_capture.capture_command(
            command, output_dir, max_bytes_per_stream=32
        )

        assert status == 0, "a successful wrapped command must stay successful"
        assert len((output_dir / "stdout.log").read_bytes()) == 32, (
            "Expected capture keeps prefix and discards output past per stream limit."
        )
        assert (
            (output_dir / "stdout.log")
            .read_bytes()
            .startswith(b"first stdout diagnostic")
        ), "Expected the stdout prefix to be retained."
        assert len((output_dir / "stderr.log").read_bytes()) == 32, (
            "Expected capture keeps prefix and discards output past per stream limit."
        )
        assert (
            (output_dir / "stderr.log")
            .read_bytes()
            .startswith(b"first actionable stderr")
        ), "Expected the stderr prefix to be retained."
        metadata = json.loads((output_dir / "metadata.json").read_text())
        assert metadata["stdout"]["truncated_bytes"] == len(stdout.encode()) - 32, (
            "Expected capture keeps prefix and discards output past per stream limit."
        )
        assert metadata["stderr"]["truncated_bytes"] == len(stderr.encode()) - 32, (
            "Expected capture keeps prefix and discards output past per stream limit."
        )

    def test_successful_execution_writes_complete_capture(self, tmp_path: Path) -> None:
        """Successful mutation execution has complete, zero-status metadata."""
        output_dir = tmp_path / "diagnostics"

        status = mutation_capture.capture_command(
            [sys.executable, "-c", "print('mutation run complete')"], output_dir
        )

        assert status == 0, "Expected successful execution writes complete capture."
        metadata = json.loads((output_dir / "metadata.json").read_text())
        assert metadata["state"] == "complete", (
            "Expected successful execution writes complete capture."
        )
        assert metadata["exit_code"] == 0, (
            "Expected successful execution writes complete capture."
        )

    def test_unstartable_command_is_retained_as_startup_diagnostic(
        self, tmp_path: Path
    ) -> None:
        """A failure before mutmut starts still produces diagnostic evidence."""
        output_dir = tmp_path / "diagnostics"

        status = mutation_capture.capture_command(
            [str(tmp_path / "missing-command")], output_dir
        )

        assert status == 127, (
            "Expected unstartable command is retained as startup diagnostic."
        )
        assert "Could not start" in (output_dir / "stderr.log").read_text(), (
            "Expected unstartable command is retained as startup diagnostic."
        )
        metadata = json.loads((output_dir / "metadata.json").read_text())
        assert metadata["state"] == "complete", (
            "Expected unstartable command is retained as startup diagnostic."
        )
        assert metadata["exit_code"] == 127, (
            "Expected unstartable command is retained as startup diagnostic."
        )
