"""Tests for core stage_artefacts behaviour: copy, optional, checksum, templates."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest
from stage_common import StageError
from stage_common.config import ArtefactConfig
from stage_common.pipeline import stage_artefacts

from conftest import make_linux_config


class TestStageArtefactsCore:
    """Tests for core stage_artefacts behaviour."""

    def test_stages_artefact(self, tmp_path: Path) -> None:
        """Artefacts are copied to staging directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        config = make_linux_config(
            workspace, [ArtefactConfig(source="myapp", output="binary_path")]
        )

        result = stage_artefacts(config)

        assert len(result.staged_artefacts) == 1
        assert result.staging_dir.exists()
        assert (result.staging_dir / "myapp").exists()
        assert "myapp" in result.checksums

    def test_raises_for_missing_required_artefact(self, tmp_path: Path) -> None:
        """Missing required artefacts raise StageError."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = make_linux_config(
            workspace, [ArtefactConfig(source="missing", required=True)]
        )

        with pytest.raises(StageError, match="not found"):
            stage_artefacts(config)

    def test_skips_optional_artefact(self, tmp_path: Path) -> None:
        """Missing optional artefacts are skipped."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        config = make_linux_config(
            workspace,
            [
                ArtefactConfig(source="myapp"),
                ArtefactConfig(source="optional", required=False),
            ],
        )

        result = stage_artefacts(config)

        assert len(result.staged_artefacts) == 1
        assert result.skipped_artefacts == ["optional"]

    def test_uses_alternative_when_primary_missing(self, tmp_path: Path) -> None:
        """Alternative source is used when primary source is missing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Primary source does not exist, but alternative does
        alt_source = workspace / "fallback"
        alt_source.write_text("fallback content", encoding="utf-8")

        config = make_linux_config(
            workspace,
            [
                ArtefactConfig(
                    source="primary",
                    alternatives=["fallback"],
                ),
            ],
        )

        result = stage_artefacts(config)

        assert len(result.staged_artefacts) == 1
        staged_file = result.staged_artefacts[0]
        assert staged_file.name == "fallback"
        assert staged_file.read_text(encoding="utf-8") == "fallback content"

    def test_generates_checksum_sidecar(self, tmp_path: Path) -> None:
        """Checksum sidecar files are generated."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source = workspace / "myapp"
        source.write_text("binary content", encoding="utf-8")

        config = make_linux_config(workspace, [ArtefactConfig(source="myapp")])

        result = stage_artefacts(config)

        checksum_file = result.staging_dir / "myapp.sha256"
        assert checksum_file.exists()
        contents = checksum_file.read_text(encoding="utf-8")
        assert "myapp" in contents

    @pytest.mark.parametrize(
        "source",
        [
            "{unknown}/x",
            "prefix/{unknown}",
            "{unknown}/{source_name}",
            "{unknown}",
            "dir/{unknown}/x",
            "{target}/{missing}",
            "target/{unknown}/release",
            "{unknown}.txt",
        ],
    )
    def test_invalid_template_key_in_source_raises(
        self, tmp_path: Path, source: str
    ) -> None:
        """Invalid source template keys raise StageError through staging."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = make_linux_config(workspace, [ArtefactConfig(source=source)])

        with pytest.raises(StageError, match="Invalid template key"):
            stage_artefacts(config)
