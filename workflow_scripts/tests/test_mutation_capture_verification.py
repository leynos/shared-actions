"""Tests that require usable mutation-failure diagnostics."""

from __future__ import annotations

import dataclasses
import json
import typing as typ

import pytest

from workflow_scripts import mutation_capture

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(frozen=True)
class IncompleteCaptureCase:
    """Metadata that describes why diagnostic capture did not complete."""

    metadata_state: str
    metadata_errors: tuple[str, ...]
    expected_detail: str


@dataclasses.dataclass(frozen=True)
class InvalidMetadataCase:
    """Malformed metadata content and the diagnostic it should produce."""

    contents: str
    expected_message: str


class TestVerifyFailureCapture:
    """Failure evidence is required when a mutation step fails."""

    def test_missing_diagnostics_fails_with_actionable_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing files fail clearly instead of becoming an upload warning."""
        status = mutation_capture.verify_failure_capture(tmp_path / "missing")

        output = capsys.readouterr().err
        assert status == 1, (
            "Expected missing diagnostics fails with actionable message."
        )
        assert (
            "ERROR: mutation failure diagnostics are missing or incomplete" in output
        ), "Expected missing diagnostics fails with actionable message."
        assert "stdout.log" in output, (
            "Expected missing diagnostics fails with actionable message."
        )
        assert "stderr.log" in output, (
            "Expected missing diagnostics fails with actionable message."
        )
        assert "The failed mutation step remains failed" in output, (
            "Expected missing diagnostics fails with actionable message."
        )

    @pytest.mark.parametrize(
        "case",
        [
            IncompleteCaptureCase("running", (), "possible timeout or cancellation"),
            IncompleteCaptureCase(
                "incomplete",
                ("capture-stderr did not reach EOF after the command exited",),
                "a pipe did not reach EOF or capture failed",
            ),
        ],
    )
    def test_incomplete_capture_reports_why_it_did_not_finish(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        case: IncompleteCaptureCase,
    ) -> None:
        """Incomplete evidence distinguishes interruption from a held pipe."""
        output_dir = tmp_path / "diagnostics"
        output_dir.mkdir()
        (output_dir / "stdout.log").touch()
        (output_dir / "stderr.log").touch()
        (output_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "state": case.metadata_state,
                    "exit_code": None,
                    "capture_errors": list(case.metadata_errors),
                }
            )
        )

        status = mutation_capture.verify_failure_capture(output_dir)

        output = capsys.readouterr().err
        assert status == 1, "Expected incomplete capture to fail verification."
        assert f"state={case.metadata_state}" in output, (
            "Expected the reported state to match the metadata."
        )
        assert case.expected_detail in output, (
            "Expected incomplete diagnostics to explain why capture ended."
        )
        if case.metadata_state == "incomplete":
            assert "possible timeout or cancellation" not in output, (
                "Expected a held pipe not to be reported as cancellation."
            )

    @pytest.mark.parametrize(
        "case",
        [
            InvalidMetadataCase("not json", "metadata.json cannot be read"),
            InvalidMetadataCase("[]", "does not contain a JSON object"),
            InvalidMetadataCase(
                json.dumps({"state": "complete", "exit_code": 0}),
                "non-zero mutation exit status",
            ),
            InvalidMetadataCase(
                json.dumps(
                    {"state": "complete", "exit_code": 7, "capture_errors": ["write"]}
                ),
                "capture errors",
            ),
        ],
    )
    def test_invalid_metadata_fails_verification(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        case: InvalidMetadataCase,
    ) -> None:
        """Malformed metadata and capture errors make failure evidence unusable."""
        output_dir = tmp_path / "diagnostics"
        output_dir.mkdir()
        (output_dir / "stdout.log").touch()
        (output_dir / "stderr.log").touch()
        (output_dir / "metadata.json").write_text(case.contents)

        status = mutation_capture.verify_failure_capture(output_dir)

        assert status == 1, "Expected invalid metadata fails verification."
        assert case.expected_message in capsys.readouterr().err, (
            "Expected invalid metadata fails verification."
        )
