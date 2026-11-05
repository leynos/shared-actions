"""Tests for bounded mutation-command diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
import threading
import time
import typing as typ
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from workflow_scripts import mutation_capture

if typ.TYPE_CHECKING:
    from pathlib import Path


class _FailingOutput:
    """A stream whose writes fail like an unavailable Actions log pipe."""

    error_message = "log pipe closed"

    @property
    def buffer(self) -> _FailingOutput:
        return self

    def write(self, value: bytes) -> int:
        raise OSError(self.error_message)

    def flush(self) -> None:
        raise OSError(self.error_message)


class _FailingInput:
    """A pipe that fails during both reading and cleanup."""

    read_error = "pipe read failed"
    close_error = "pipe close failed"

    def read(self, size: int) -> bytes:
        raise OSError(self.read_error)

    def close(self) -> None:
        raise OSError(self.close_error)


class _FailingFile:
    """A capture file that cannot store bytes."""

    error_message = "disk full"

    def write(self, value: bytes) -> int:
        raise OSError(self.error_message)

    def flush(self) -> None:
        raise OSError(self.error_message)


class _FailingCloseStack(contextlib.ExitStack):
    """An exit stack that reports a close failure after releasing callbacks."""

    error_message = "close failed"
    _should_fail_close = False

    def pop_all(self) -> _FailingCloseStack:
        transferred = typ.cast("_FailingCloseStack", super().pop_all())
        transferred._should_fail_close = True
        return transferred

    def close(self) -> None:
        should_fail = self._should_fail_close
        self._should_fail_close = False
        super().close()
        if should_fail:
            raise OSError(self.error_message)


def _capture_evidence() -> mutation_capture._CaptureEvidence:
    """Build capture evidence backed by in-memory binary files."""
    return mutation_capture._CaptureEvidence(
        32, {name: io.BytesIO() for name in ("stdout", "stderr")}
    )


@pytest.fixture
def failing_metadata_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace metadata writes with a disk-full failure."""

    def fail_write(path: Path, metadata: dict[str, object]) -> None:
        error_message = "metadata disk full"
        raise OSError(error_message)

    monkeypatch.setattr(mutation_capture, "_write_metadata", fail_write)


class TestCaptureFailurePaths:
    """Capture failures remain visible and cannot mask command failures."""

    def test_stream_write_and_forward_failures_are_recorded_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken artifact or Actions log stream becomes capture evidence."""
        evidence = _capture_evidence()
        evidence.stream_files["stdout"] = typ.cast("typ.BinaryIO", _FailingFile())

        evidence._record_chunk("stdout", b"diagnostic")
        monkeypatch.setattr(
            mutation_capture.sys,
            "stdout",
            typ.cast("typ.TextIO", _FailingOutput()),
        )
        evidence._forward_output("stdout", b"diagnostic")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(evidence._record_error, ["duplicate"] * 32))

        assert evidence.counts["stdout"]["bytes_seen"] == len(b"diagnostic"), (
            "Expected stream write and forward failures are recorded once."
        )
        assert evidence.counts["stdout"]["truncated_bytes"] == len(b"diagnostic"), (
            "Expected stream write and forward failures are recorded once."
        )
        assert evidence.capture_errors == [
            "could not save stdout diagnostics: disk full",
            "could not forward stdout to the workflow log: log pipe closed",
            "duplicate",
        ], "Expected stream write and forward failures are recorded once."

    def test_stream_reader_records_read_and_close_failures(self) -> None:
        """Pipe read and close failures both survive in diagnostic metadata."""
        evidence = _capture_evidence()

        mutation_capture._capture_stream_output(
            "stderr", typ.cast("typ.BinaryIO", _FailingInput()), evidence
        )

        assert evidence.capture_errors == [
            "could not read stderr diagnostics: pipe read failed",
            "could not close stderr output pipe: pipe close failed",
        ], "Expected stream reader records read and close failures."

    def test_recording_after_the_limit_discards_the_later_chunk(self) -> None:
        """Once the prefix is full, additional chunks are counted and dropped."""
        evidence = _capture_evidence()

        evidence._record_chunk("stdout", b"first chunk is longer than the limit")
        evidence._record_chunk("stdout", b"later chunk")

        assert evidence.counts["stdout"]["bytes_seen"] == 47, (
            "Expected recording after the limit discards the later chunk."
        )
        assert evidence.counts["stdout"]["bytes_saved"] == 32, (
            "Expected recording after the limit discards the later chunk."
        )
        assert evidence.counts["stdout"]["truncated_bytes"] == 15, (
            "Expected recording after the limit discards the later chunk."
        )

    def test_metadata_snapshot_does_not_finalize_capture(self) -> None:
        """Querying metadata leaves the evidence collector writable."""
        evidence = _capture_evidence()

        metadata = evidence._to_metadata(
            state="complete", exit_code=0, process_returncode=0
        )
        evidence._record_error("late capture diagnostic")
        evidence._record_chunk("stdout", b"late output")

        assert metadata["capture_errors"] == [], (
            "Expected the metadata query to return its original snapshot."
        )
        assert evidence.capture_errors == ["late capture diagnostic"], (
            "Expected metadata queries not to freeze capture errors."
        )
        assert evidence.counts["stdout"]["bytes_saved"] == len(b"late output"), (
            "Expected metadata queries not to stop output capture."
        )

    def test_finalization_stops_late_output_and_error_recording(self) -> None:
        """Finalizing evidence freezes state before its metadata snapshot."""
        evidence = _capture_evidence()

        evidence._finalize()
        evidence._record_error("late capture diagnostic")
        evidence._record_chunk("stdout", b"late output")

        assert evidence.capture_errors == [], (
            "Expected finalization to ignore later capture errors."
        )
        assert evidence.counts["stdout"]["bytes_seen"] == 0, (
            "Expected finalization to stop later output recording."
        )

    def test_missing_process_pipes_are_terminated_and_reported(self) -> None:
        """A malformed subprocess cannot leave the wrapper waiting forever."""
        evidence = _capture_evidence()
        process = mock.Mock(stdout=None, stderr=None)
        process.wait.return_value = 19

        status = mutation_capture._capture_process_output(
            typ.cast("typ.Any", process), evidence
        )

        assert status == 19, (
            "Expected missing process pipes are terminated and reported."
        )
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()
        assert evidence.capture_errors == [
            "the mutation process did not provide both output pipes"
        ], "Expected missing process pipes are terminated and reported."

    def test_inherited_pipe_does_not_hold_capture_open_after_parent_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A descendant holding a pipe cannot delay the wrapper past its grace."""
        monkeypatch.setattr(
            mutation_capture, "PIPE_DRAIN_GRACE_SECONDS", 0.1, raising=False
        )
        release_file = tmp_path / "release-child"
        finished_file = tmp_path / "child-finished"
        child_script = "\n".join(
            (
                "import pathlib",
                "import time",
                f"release_file = pathlib.Path({str(release_file)!r})",
                "while not release_file.exists():",
                "    time.sleep(0.01)",
                f"pathlib.Path({str(finished_file)!r}).touch()",
            )
        )
        parent_script = "\n".join(
            (
                "import subprocess",
                "import sys",
                f"subprocess.Popen([sys.executable, '-c', {child_script!r}])",
            )
        )
        release_timer = threading.Timer(3, release_file.touch)
        release_timer.daemon = True
        release_timer.start()
        started_at = time.monotonic()
        output_dir = tmp_path / "diagnostics"

        try:
            status = mutation_capture.capture_command(
                [sys.executable, "-c", parent_script], output_dir
            )
            elapsed = time.monotonic() - started_at

            assert elapsed < 2.5, (
                "Expected inherited pipe does not hold capture open after parent exit."
            )
            assert status == 1, (
                "Expected inherited pipe does not hold capture open after parent exit."
            )
            metadata = json.loads((output_dir / "metadata.json").read_text())
            assert metadata["state"] == "incomplete", (
                "Expected inherited pipe does not hold capture open after parent exit."
            )
            assert any(
                "did not reach EOF" in error for error in metadata["capture_errors"]
            ), "Expected inherited pipe does not hold capture open after parent exit."
        finally:
            release_timer.cancel()
            release_file.touch()
            deadline = time.monotonic() + 2
            while not finished_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        assert finished_file.exists(), "the inherited-pipe child must be released"

    @pytest.mark.parametrize(
        ("process_returncode", "expected_status"), [(0, 1), (23, 23), (-9, 137)]
    )
    def test_capture_errors_fail_success_but_preserve_process_status(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        process_returncode: int,
        expected_status: int,
    ) -> None:
        """Capture errors fail success while a baseline error keeps its code."""
        evidence = _capture_evidence()
        evidence._record_error("could not forward stderr")

        status = mutation_capture._finalize_capture(
            evidence, tmp_path / "metadata.json", process_returncode
        )

        assert status == expected_status, (
            "Expected capture errors fail success but preserve process status."
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        assert metadata["exit_code"] == expected_status, (
            "Expected capture errors fail success but preserve process status."
        )
        assert metadata["process_returncode"] == process_returncode, (
            "Expected capture errors fail success but preserve process status."
        )
        if process_returncode == 0:
            assert "diagnostic capture failed" in capsys.readouterr().err, (
                "Expected capture errors fail success but preserve process status."
            )

    @pytest.mark.usefixtures("failing_metadata_write")
    @pytest.mark.parametrize("status_pair", [(0, 1), (23, 23)])
    def test_metadata_write_failure_never_changes_failed_status(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        status_pair: tuple[int, int],
    ) -> None:
        """Metadata failure is actionable and cannot turn a baseline red green."""
        process_status, expected_status = status_pair

        status = mutation_capture._finalize_capture(
            _capture_evidence(), tmp_path / "metadata.json", process_status
        )

        assert status == expected_status, (
            "Expected metadata write failure never changes failed status."
        )
        assert (
            "cannot finalize mutation diagnostic metadata" in capsys.readouterr().err
        ), "Expected metadata write failure never changes failed status."

    @pytest.mark.parametrize(
        "capture_request",
        [([], 32, "capture mode requires a command"), (["true"], 0, "limit")],
    )
    def test_invalid_capture_request_fails_before_creating_evidence(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        capture_request: tuple[list[str], int, str],
    ) -> None:
        """Invalid commands and limits fail before creating diagnostics."""
        command, limit, message = capture_request
        output_dir = tmp_path / "diagnostics"

        status = mutation_capture.capture_command(
            command, output_dir, max_bytes_per_stream=limit
        )

        assert status == 2, (
            "Expected invalid capture request fails before creating evidence."
        )
        assert message in capsys.readouterr().err, (
            "Expected invalid capture request fails before creating evidence."
        )
        assert not output_dir.exists(), (
            "Expected invalid capture request fails before creating evidence."
        )

    def test_output_directory_creation_failure_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A non-directory parent produces a direct capture failure."""
        parent = tmp_path / "file"
        parent.write_text("occupied")

        status = mutation_capture.capture_command(
            [sys.executable, "-c", "pass"], parent / "diagnostics"
        )

        assert status == 1, "Expected output directory creation failure is reported."
        assert "cannot create mutation diagnostics" in capsys.readouterr().err, (
            "Expected output directory creation failure is reported."
        )

    @pytest.mark.usefixtures("failing_metadata_write")
    def test_initial_metadata_failure_prevents_command_start(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A failed running marker stops before mutation execution begins."""
        monkeypatch.setattr(
            mutation_capture,
            "_run_command",
            lambda command, evidence: pytest.fail("mutation command must not start"),
        )

        status = mutation_capture.capture_command(
            [sys.executable, "-c", "pass"], tmp_path / "diagnostics"
        )

        assert status == 1, "Expected initial metadata failure prevents command start."
        assert (
            "cannot initialize mutation diagnostic metadata" in capsys.readouterr().err
        ), "Expected a clear initial metadata failure."

    def test_stream_close_failure_fails_successful_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A close failure is retained and turns otherwise successful capture red."""
        monkeypatch.setattr(contextlib, "ExitStack", _FailingCloseStack)
        monkeypatch.setattr(
            mutation_capture, "_run_command", lambda command, evidence: 0
        )
        output_dir = tmp_path / "diagnostics"

        status = mutation_capture.capture_command(["unused"], output_dir)

        assert status == 1, "Expected stream close failure fails successful capture."
        metadata = json.loads((output_dir / "metadata.json").read_text())
        assert metadata["capture_errors"] == [
            "could not close a diagnostic stream: close failed"
        ], "Expected stream close failure fails successful capture."

    def test_main_runs_wrapped_command_and_routes_verification(
        self, tmp_path: Path
    ) -> None:
        """The CLI dispatches wrapped commands and completed failure evidence."""
        output_dir = tmp_path / "diagnostics"
        status = mutation_capture.main(
            [
                "--output-dir",
                str(output_dir),
                "--max-bytes-per-stream",
                "64",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(23)",
            ]
        )

        assert status == 23, (
            "Expected main runs wrapped command and routes verification."
        )
        assert (
            mutation_capture.main(["--output-dir", str(output_dir), "--verify-failure"])
            == 0
        ), "Expected completed failure evidence to verify successfully."

    def test_module_entrypoint_exits_with_the_wrapped_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running the script entrypoint returns the wrapped process status."""
        assert mutation_capture.__file__ is not None, (
            "Expected module entrypoint exits with the wrapped status."
        )
        output_dir = tmp_path / "diagnostics"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                mutation_capture.__file__,
                "--output-dir",
                str(output_dir),
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(23)",
            ],
        )

        with pytest.raises(SystemExit) as raised:
            runpy.run_path(mutation_capture.__file__, run_name="__main__")

        assert raised.value.code == 23, (
            "Expected module entrypoint exits with the wrapped status."
        )

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--output-dir", "diagnostics"],
            ["--output-dir", "diagnostics", "--verify-failure", "--", "true"],
        ],
    )
    def test_main_rejects_missing_or_extra_commands(self, arguments: list[str]) -> None:
        """The CLI rejects an absent command and commands in verify mode."""
        with pytest.raises(SystemExit) as raised:
            mutation_capture.main(arguments)

        assert raised.value.code == 2, (
            "Expected main rejects missing or extra commands."
        )
