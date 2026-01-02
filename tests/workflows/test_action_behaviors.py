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
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

from .conftest import ActConfig, run_act, skip_unless_act, skip_unless_workflow_tests

# Sentinel prefix indicating a path relative to the temp base directory.
_TEMP_PATH_PREFIX = "@temp:"


@dataclasses.dataclass(slots=True)
class WorkflowRun:
    """Specification for a workflow run."""

    workflow: str
    event: str
    job: str


@dataclasses.dataclass(slots=True)
class EnvOverrideTestCase:
    """Test case specification for environment override tests."""

    workflow: str
    job: str
    container_env_template: dict[str, str]
    expected_patterns: list[tuple[str, str]]


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


def _resolve_container_env(
    template: dict[str, str], temp_base_dir: Path
) -> dict[str, str]:
    """Resolve container env template, expanding @temp: prefixed paths."""
    resolved: dict[str, str] = {}
    for key, value in template.items():
        if value.startswith(_TEMP_PATH_PREFIX):
            relative_path = value.removeprefix(_TEMP_PATH_PREFIX)
            resolved[key] = str(temp_base_dir / relative_path)
        else:
            resolved[key] = value
    return resolved


@skip_unless_act
@skip_unless_workflow_tests
@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            EnvOverrideTestCase(
                workflow="test-export-cargo-metadata.yml",
                job="test-export-metadata-env-overrides",
                container_env_template={"INPUT_FIELDS": "name"},
                expected_patterns=[
                    (r'name["\s]*[:=]["\s]*\S+', "name= not found in logs"),
                    (r'version["\s]*[:=]', "version= not found in logs"),
                ],
            ),
            id="export-cargo-metadata",
        ),
        pytest.param(
            EnvOverrideTestCase(
                workflow="test-stage-release-artefacts.yml",
                job="test-stage-artefacts-env-overrides",
                container_env_template={
                    "INPUT_CONFIG_FILE": "@temp:stage-workspace/test-staging.toml",
                    "INPUT_TARGET": "linux-x86_64",
                },
                expected_patterns=[
                    (
                        r'artifact[-_]dir["\s]*[:=]["\s]*\S+',
                        "artifact-dir not found or empty in logs",
                    ),
                    (
                        r'staged[-_]files["\s]*[:=]["\s]*\S+',
                        "staged-files not found or empty in logs",
                    ),
                ],
            ),
            id="stage-release-artefacts",
        ),
        pytest.param(
            EnvOverrideTestCase(
                workflow="test-upload-release-assets.yml",
                job="test-upload-assets-env-overrides",
                container_env_template={
                    "INPUT_RELEASE_TAG": "v9.9.9",
                    "INPUT_BIN_NAME": "test-app",
                    "INPUT_DIST_DIR": "@temp:release-assets-dist",
                    "INPUT_DRY_RUN": "true",
                },
                expected_patterns=[
                    (
                        r'uploaded[-_]count["\s]*[:=]["\s]*\d+',
                        "uploaded-count not found in logs",
                    ),
                    (
                        r'upload[-_]error["\s]*[:=]["\s]*false',
                        "upload-error=false not found in logs",
                    ),
                ],
            ),
            id="upload-release-assets",
        ),
    ],
)
def test_env_overrides_normalize_inputs(
    artifact_dir: Path,
    temp_base_dir: Path,
    test_case: EnvOverrideTestCase,
) -> None:
    """Container env vars override default workflow values via step outputs."""
    container_env = _resolve_container_env(
        test_case.container_env_template, temp_base_dir
    )
    logs = _run_act_and_get_logs(
        run=WorkflowRun(
            workflow=test_case.workflow, event="pull_request", job=test_case.job
        ),
        artifact_dir=artifact_dir,
        container_env=container_env,
    )

    _assert_log_patterns(logs, test_case.expected_patterns, flags=re.IGNORECASE)


@skip_unless_act
@skip_unless_workflow_tests
@pytest.mark.parametrize(
    ("workflow", "job", "expected_patterns"),
    [
        pytest.param(
            "test-stage-release-artefacts.yml",
            "test-stage-artefacts",
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
            id="stage-release-artefacts",
        ),
        pytest.param(
            "test-upload-release-assets.yml",
            "test-upload-assets-dry-run",
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
            id="upload-release-assets-dry-run",
        ),
        pytest.param(
            "test-rust-build-release-root-discovery.yml",
            "test-action-setup-root",
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
            id="rust-build-release-root-discovery",
        ),
    ],
)
def test_simple_workflow_validation(
    artifact_dir: Path,
    workflow: str,
    job: str,
    expected_patterns: list[tuple[str, str]],
) -> None:
    """Validate workflow outputs match expected patterns."""
    logs = _run_act_and_get_logs(
        run=WorkflowRun(workflow=workflow, event="pull_request", job=job),
        artifact_dir=artifact_dir,
    )

    _assert_log_patterns(logs, expected_patterns, flags=re.IGNORECASE)


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
