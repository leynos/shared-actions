"""Behavioural tests for composite actions using act.

These tests invoke the composite actions via act to verify end-to-end behaviour.
They require Docker/Podman and act to be available, and must be opted into via
the ACT_WORKFLOW_TESTS=1 environment variable.

Run with:
    ACT_WORKFLOW_TESTS=1 sudo -E make test
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from .conftest import ActConfig, run_act, skip_unless_act, skip_unless_workflow_tests

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artifacts"


def _run_act_and_get_logs(
    workflow: str, event: str, job: str, artifact_dir: Path
) -> str:
    """Run act and assert success, returning logs for further assertions.

    Parameters
    ----------
    workflow
        Path to the workflow file relative to .github/workflows/.
    event
        GitHub event type (push, pull_request, workflow_call, etc.).
    job
        Job name to run.
    artifact_dir
        Directory to store artefacts.

    Returns
    -------
    str
        Combined stdout/stderr logs from the act run.
    """
    config = ActConfig(artifact_dir=artifact_dir)
    code, logs = run_act(workflow, event, job, config)
    assert code == 0, f"act failed:\n{logs}"
    return logs


@skip_unless_act
@skip_unless_workflow_tests
class TestDetermineReleaseModes:
    """Behavioural tests for the determine-release-modes action."""

    @pytest.mark.parametrize(
        ("event", "description"),
        [
            ("workflow_call", "Workflow call with dry-run input"),
            ("pull_request", "Pull request event defaults to dry-run mode"),
        ],
    )
    def test_dry_run_mode(
        self, artifact_dir: Path, event: str, description: str
    ) -> None:
        """Verify dry-run mode is enabled for specific events."""
        logs = _run_act_and_get_logs(
            workflow="test-determine-release-modes.yml",
            event=event,
            job="test-determine-modes",
            artifact_dir=artifact_dir,
        )

        assert re.search(r'dry[-_]run["\s]*[:=]["\s]*true', logs, re.IGNORECASE), (
            f"{description}: dry-run=true not found in logs"
        )

    def test_push_tag_event(self, artifact_dir: Path) -> None:
        """Push tag event enables publishing."""
        logs = _run_act_and_get_logs(
            workflow="test-determine-release-modes.yml",
            event="push",
            job="test-determine-modes",
            artifact_dir=artifact_dir,
        )

        assert re.search(
            r'should[-_]publish["\s]*[:=]["\s]*true', logs, re.IGNORECASE
        ), "Push tag event: should-publish=true not found in logs"


@skip_unless_act
@skip_unless_workflow_tests
class TestExportCargoMetadata:
    """Behavioural tests for the export-cargo-metadata action."""

    def test_exports_cargo_metadata(self, artifact_dir: Path) -> None:
        """Action exports name and version from Cargo.toml."""
        logs = _run_act_and_get_logs(
            workflow="test-export-cargo-metadata.yml",
            event="pull_request",
            job="test-export-metadata",
            artifact_dir=artifact_dir,
        )

        # Verify metadata was exported with non-empty values
        assert re.search(r'name["\s]*[:=]["\s]*\S+', logs), (
            "name= not found or empty in logs"
        )
        assert re.search(r'version["\s]*[:=]["\s]*\S+', logs), (
            "version= not found or empty in logs"
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestStageReleaseArtefacts:
    """Behavioural tests for the stage-release-artefacts action."""

    def test_stages_artefacts(self, artifact_dir: Path) -> None:
        """Action stages artefacts and creates checksum sidecars."""
        logs = _run_act_and_get_logs(
            workflow="test-stage-release-artefacts.yml",
            event="pull_request",
            job="test-stage-artefacts",
            artifact_dir=artifact_dir,
        )

        # Verify staging completed with actual values
        assert re.search(r'artifact[-_]dir["\s]*[:=]["\s]*\S+', logs), (
            "artifact-dir not found or empty in logs"
        )
        assert re.search(r'staged[-_]files["\s]*[:=]["\s]*\S+', logs), (
            "staged-files not found or empty in logs"
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestUploadReleaseAssets:
    """Behavioural tests for the upload-release-assets action."""

    def test_dry_run_validates_assets(self, artifact_dir: Path) -> None:
        """Dry-run mode validates assets without uploading."""
        logs = _run_act_and_get_logs(
            workflow="test-upload-release-assets.yml",
            event="pull_request",
            job="test-upload-assets-dry-run",
            artifact_dir=artifact_dir,
        )

        # Verify dry-run processed assets with specific values
        uploaded_match = re.search(r'uploaded[-_]count["\s]*[:=]["\s]*(\d+)', logs)
        error_match = re.search(
            r'upload[-_]error["\s]*[:=]["\s]*false', logs, re.IGNORECASE
        )
        assert uploaded_match, "uploaded-count not found in logs"
        assert uploaded_match.group(1), "uploaded-count has no value in logs"
        assert error_match, "upload-error=false not found in logs"
