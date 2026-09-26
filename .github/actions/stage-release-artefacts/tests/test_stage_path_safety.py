"""Property and parametrized tests for path-traversal rejection."""

from __future__ import annotations

import time
import typing as typ

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from stage_common.config import ArtefactConfig
from stage_common.pipeline import stage_artefacts

from conftest import (
    HYPOTHESIS_SETTINGS,
    TEMPLATE_SAFE_PATH_SEGMENTS,
    TRAVERSAL_DESTINATIONS,
    _assert_path_traversal_rejected,
    make_linux_config,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestStageArtefactsPathSafety:
    """Property and parametrized tests for path-traversal rejection."""

    @pytest.mark.parametrize(
        "destination",
        [
            "../../escape/file",
            "../escape/file",
            "nested/../../../escape/file",
            "../file",
            "nested/../../file",
            "nested/deeper/../../../file",
        ],
    )
    def test_path_traversal_destination_raises(
        self, tmp_path: Path, destination: str
    ) -> None:
        """Destination paths escaping the staging directory raise StageError."""
        _assert_path_traversal_rejected(tmp_path, destination)

    @HYPOTHESIS_SETTINGS
    @given(
        segments=st.lists(
            TEMPLATE_SAFE_PATH_SEGMENTS,
            min_size=1,
            max_size=4,
        )
    )
    def test_safe_destination_paths_stay_under_staging_dir_through_staging(
        self, tmp_path: Path, segments: list[str]
    ) -> None:
        """Generated relative destinations remain under the staging directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "myapp").write_text("binary content", encoding="utf-8")
        destination = "/".join(segments)
        config = make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="myapp",
                    destination=destination,
                )
            ],
        )

        result = stage_artefacts(config)

        staged = result.staged_artefacts[0]
        assert staged.is_relative_to(result.staging_dir), (
            f"staged artefact path is not inside staging_dir: "
            f"{staged} not relative to {result.staging_dir}"
        )

    @HYPOTHESIS_SETTINGS
    @given(destination=TRAVERSAL_DESTINATIONS)
    def test_parent_traversal_destinations_are_rejected_through_staging(
        self, tmp_path: Path, destination: str
    ) -> None:
        """Generated parent traversal destinations cannot escape staging."""
        _assert_path_traversal_rejected(tmp_path, f"../{destination}")


def test_the_shared_property_settings_set_no_deadline() -> None:
    """Keep host load from failing the staging properties (#487).

    The properties that share these settings stage real files, so a
    wall-clock deadline would assert the host's load rather than the code.
    The deadline must be absent, not merely generous.
    """
    assert HYPOTHESIS_SETTINGS.deadline is None


def test_a_property_on_the_shared_settings_outlives_any_deadline() -> None:
    """A property slower than Hypothesis's default deadline still passes (#520).

    Each example sleeps past the default 200 ms deadline, standing in for a
    staging example on a contended host. Only the example count is narrowed,
    so the deadline is whatever the shared settings carry: were it inherited
    from the default, this would raise ``DeadlineExceeded``.
    """
    slow_example_seconds = 0.3

    @settings(HYPOTHESIS_SETTINGS, max_examples=2, database=None)
    @given(st.integers())
    def property_slower_than_the_default_deadline(_value: int) -> None:
        time.sleep(slow_example_seconds)

    property_slower_than_the_default_deadline()
