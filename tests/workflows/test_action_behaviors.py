"""Behavioral tests for composite actions using act.

These tests invoke the composite actions via act to verify end-to-end behavior.
They require Docker/Podman and act to be available, and must be opted into via
the ACT_WORKFLOW_TESTS=1 environment variable.

Run with:
    ACT_WORKFLOW_TESTS=1 sudo -E make test
"""

from __future__ import annotations

import typing as typ

import pytest

from .conftest import run_act, skip_unless_act, skip_unless_workflow_tests

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artifacts"


@skip_unless_act
@skip_unless_workflow_tests
class TestDetermineReleaseModes:
    """Behavioral tests for the determine-release-modes action."""

    def test_workflow_call_dry_run(self, artifact_dir: Path) -> None:
        """Workflow call with dry-run input produces expected outputs."""
        code, logs = run_act(
            workflow="test-determine-release-modes.yml",
            event="workflow_call",
            job="test-determine-modes",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        assert "dry_run=true" in logs or '"dry_run":"true"' in logs

    def test_push_tag_event(self, artifact_dir: Path) -> None:
        """Push tag event enables publishing."""
        code, logs = run_act(
            workflow="test-determine-release-modes.yml",
            event="push",
            job="test-determine-modes",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        # On push, should_publish should be true
        assert "should_publish=true" in logs or '"should_publish":"true"' in logs

    def test_pull_request_defaults_to_dry_run(self, artifact_dir: Path) -> None:
        """Pull request event defaults to dry-run mode."""
        code, logs = run_act(
            workflow="test-determine-release-modes.yml",
            event="pull_request",
            job="test-determine-modes",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        assert "dry_run=true" in logs or '"dry_run":"true"' in logs


@skip_unless_act
@skip_unless_workflow_tests
class TestExportCargoMetadata:
    """Behavioral tests for the export-cargo-metadata action."""

    def test_exports_cargo_metadata(self, artifact_dir: Path) -> None:
        """Action exports name and version from Cargo.toml."""
        code, logs = run_act(
            workflow="test-export-cargo-metadata.yml",
            event="pull_request",
            job="test-export-metadata",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        # Verify metadata was exported (check for output patterns in logs)
        assert "name=" in logs
        assert "version=" in logs


@skip_unless_act
@skip_unless_workflow_tests
class TestStageReleaseArtefacts:
    """Behavioral tests for the stage-release-artefacts action."""

    def test_stages_artefacts(self, artifact_dir: Path) -> None:
        """Action stages artefacts and creates checksum sidecars."""
        code, logs = run_act(
            workflow="test-stage-release-artefacts.yml",
            event="pull_request",
            job="test-stage-artefacts",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        # Verify staging completed
        assert "artifact_dir=" in logs or "artifact-dir=" in logs
        assert "staged_files=" in logs or "staged-files=" in logs


@skip_unless_act
@skip_unless_workflow_tests
class TestUploadReleaseAssets:
    """Behavioral tests for the upload-release-assets action."""

    def test_dry_run_validates_assets(self, artifact_dir: Path) -> None:
        """Dry-run mode validates assets without uploading."""
        code, logs = run_act(
            workflow="test-upload-release-assets.yml",
            event="pull_request",
            job="test-upload-assets-dry-run",
            artifact_dir=artifact_dir,
        )

        assert code == 0, f"act failed:\n{logs}"
        # Verify dry-run processed assets
        assert "uploaded_count=" in logs
        assert "upload_error=false" in logs or '"upload_error":"false"' in logs
