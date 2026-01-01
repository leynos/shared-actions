"""Behavioural tests for composite actions using act.

These tests invoke the composite actions via act to verify end-to-end behaviour.
They require Docker/Podman and act to be available, and must be opted into via
the ACT_WORKFLOW_TESTS=1 environment variable.

Run with:
    ACT_WORKFLOW_TESTS=1 sudo -E make test
"""

from __future__ import annotations

import dataclasses
import re
import tempfile
from pathlib import Path

import pytest

from .conftest import ActConfig, run_act, skip_unless_act, skip_unless_workflow_tests


def _get_temp_dir() -> Path:
    """Return the system temporary directory path."""
    return Path(tempfile.gettempdir())


@dataclasses.dataclass(slots=True)
class WorkflowRun:
    """Specification for a workflow run."""

    workflow: str
    event: str
    job: str


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for act artefacts."""
    return tmp_path / "act-artifacts"


def _run_act_and_get_logs(
    run: WorkflowRun,
    artifact_dir: Path,
    *,
    container_env: dict[str, str] | None = None,
) -> str:
    """Run act with the given workflow specification and return logs.

    Parameters
    ----------
    run
        Workflow, event, and job specification.
    artifact_dir
        Directory to store artefacts.
    container_env
        Optional environment variables to pass into the act container.

    Returns
    -------
    str
        Combined stdout/stderr logs from the act run.
    """
    config = ActConfig(artifact_dir=artifact_dir, container_env=container_env)
    code, logs = run_act(run.workflow, run.event, run.job, config)
    assert code == 0, f"act failed:\n{logs}"
    return logs


def _assert_log_patterns(
    logs: str, patterns: list[tuple[str, str]], *, flags: int = 0
) -> None:
    """Assert that all regex patterns are found in logs.

    Parameters
    ----------
    logs
        Combined stdout/stderr from act execution.
    patterns
        List of (regex_pattern, error_message) tuples to assert.
    flags
        Optional regex flags (e.g., re.IGNORECASE).
    """
    for pattern, error_message in patterns:
        assert re.search(pattern, logs, flags), error_message


@skip_unless_act
@skip_unless_workflow_tests
class TestDetermineReleaseModes:
    """Behavioural tests for the determine-release-modes action."""

    @pytest.mark.parametrize(
        ("event", "pattern", "description"),
        [
            (
                "workflow_call",
                r'dry[-_]run["\s]*[:=]["\s]*true',
                "Workflow call with dry-run input",
            ),
            (
                "pull_request",
                r'dry[-_]run["\s]*[:=]["\s]*true',
                "Pull request event defaults to dry-run mode",
            ),
            (
                "push",
                r'should[-_]publish["\s]*[:=]["\s]*true',
                "Push tag event enables publishing",
            ),
        ],
    )
    def test_release_mode_detection(
        self, artifact_dir: Path, event: str, pattern: str, description: str
    ) -> None:
        """Verify release mode outputs for different event types."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-determine-release-modes.yml",
                event=event,
                job="test-determine-modes",
            ),
            artifact_dir=artifact_dir,
        )

        _assert_log_patterns(
            logs,
            [(pattern, f"{description}: expected pattern not found in logs")],
            flags=re.IGNORECASE,
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestExportCargoMetadata:
    """Behavioural tests for the export-cargo-metadata action."""

    def test_exports_cargo_metadata(self, artifact_dir: Path) -> None:
        """Action exports name and version from Cargo.toml."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-export-cargo-metadata.yml",
                event="pull_request",
                job="test-export-metadata",
            ),
            artifact_dir=artifact_dir,
        )

        # Verify metadata was exported with non-empty values
        _assert_log_patterns(
            logs,
            [
                (r'name["\s]*[:=]["\s]*\S+', "name= not found or empty in logs"),
                (r'version["\s]*[:=]["\s]*\S+', "version= not found or empty in logs"),
            ],
        )

    def test_env_overrides_normalize_inputs(self, artifact_dir: Path) -> None:
        """Container env vars override default workflow values via step outputs."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-export-cargo-metadata.yml",
                event="pull_request",
                job="test-export-metadata-env-overrides",
            ),
            artifact_dir=artifact_dir,
            container_env={"INPUT_FIELDS": "name"},
        )

        _assert_log_patterns(
            logs,
            [
                (r'name["\s]*[:=]["\s]*\S+', "name= not found in logs"),
                (r'version["\s]*[:=]', "version= not found in logs"),
            ],
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestStageReleaseArtefacts:
    """Behavioural tests for the stage-release-artefacts action."""

    def test_stages_artefacts(self, artifact_dir: Path) -> None:
        """Action stages artefacts and creates checksum sidecars."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-stage-release-artefacts.yml",
                event="pull_request",
                job="test-stage-artefacts",
            ),
            artifact_dir=artifact_dir,
        )

        # Verify staging completed with actual values
        _assert_log_patterns(
            logs,
            [
                (
                    r'artifact[-_]dir["\s]*[:=]["\s]*\S+',
                    "artifact-dir not found or empty in logs",
                ),
                (
                    r'staged[-_]files["\s]*[:=]["\s]*\S+',
                    "staged-files not found or empty in logs",
                ),
            ],
        )

    def test_env_overrides_normalize_inputs(self, artifact_dir: Path) -> None:
        """Container env vars override default workflow values via step outputs."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-stage-release-artefacts.yml",
                event="pull_request",
                job="test-stage-artefacts-env-overrides",
            ),
            artifact_dir=artifact_dir,
            container_env={
                "INPUT_CONFIG_FILE": str(
                    _get_temp_dir() / "stage-workspace" / "test-staging.toml"
                ),
                "INPUT_TARGET": "linux-x86_64",
            },
        )

        _assert_log_patterns(
            logs,
            [
                (
                    r'artifact[-_]dir["\s]*[:=]["\s]*\S+',
                    "artifact-dir not found or empty in logs",
                ),
                (
                    r'staged[-_]files["\s]*[:=]["\s]*\S+',
                    "staged-files not found or empty in logs",
                ),
            ],
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestUploadReleaseAssets:
    """Behavioural tests for the upload-release-assets action."""

    def test_dry_run_validates_assets(self, artifact_dir: Path) -> None:
        """Dry-run mode validates assets without uploading."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-upload-release-assets.yml",
                event="pull_request",
                job="test-upload-assets-dry-run",
            ),
            artifact_dir=artifact_dir,
        )

        _assert_log_patterns(
            logs,
            [
                (
                    r'uploaded[-_]count["\s]*[:=]["\s]*\d+',
                    "uploaded-count not found in logs",
                ),
                (
                    r'upload[-_]error["\s]*[:=]["\s]*false',
                    "upload-error=false not found in logs",
                ),
            ],
            flags=re.IGNORECASE,
        )

    def test_env_overrides_normalize_inputs(self, artifact_dir: Path) -> None:
        """Container env vars override default workflow values via step outputs."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-upload-release-assets.yml",
                event="pull_request",
                job="test-upload-assets-env-overrides",
            ),
            artifact_dir=artifact_dir,
            container_env={
                "INPUT_RELEASE_TAG": "v9.9.9",
                "INPUT_BIN_NAME": "test-app",
                "INPUT_DIST_DIR": str(_get_temp_dir() / "release-assets-dist"),
                "INPUT_DRY_RUN": "true",
            },
        )

        _assert_log_patterns(
            logs,
            [
                (
                    r'uploaded[-_]count["\s]*[:=]["\s]*\d+',
                    "uploaded-count not found in logs",
                ),
                (
                    r'upload[-_]error["\s]*[:=]["\s]*false',
                    "upload-error=false not found in logs",
                ),
            ],
            flags=re.IGNORECASE,
        )


@skip_unless_act
@skip_unless_workflow_tests
class TestRustBuildReleaseRootDiscovery:
    """Behavioural tests for rust-build-release root discovery."""

    def test_action_setup_root_discovery(self, artifact_dir: Path) -> None:
        """Action setup locates action.yml and repo root from a subdirectory."""
        logs = _run_act_and_get_logs(
            run=WorkflowRun(
                workflow="test-rust-build-release-root-discovery.yml",
                event="pull_request",
                job="test-action-setup-root",
            ),
            artifact_dir=artifact_dir,
        )

        # Verify root discovery completed successfully
        _assert_log_patterns(
            logs,
            [
                (
                    r'ACTION_PATH["\s]*[:=]["\s]*\S+',
                    "ACTION_PATH not found in logs",
                ),
                (
                    r'REPO_ROOT["\s]*[:=]["\s]*\S+',
                    "REPO_ROOT not found in logs",
                ),
            ],
        )
