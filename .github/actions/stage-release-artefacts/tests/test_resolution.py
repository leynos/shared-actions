"""Tests for stage-release-artefacts path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common.resolution import match_candidate_path


class TestMatchCandidatePath:
    """Tests for the match_candidate_path function."""

    def test_matches_direct_path(self, tmp_path: Path) -> None:
        """Matches a direct file path."""
        target = tmp_path / "myapp"
        target.write_text("binary", encoding="utf-8")

        result = match_candidate_path(tmp_path, "myapp")

        assert result == target

    def test_matches_glob_pattern(self, tmp_path: Path) -> None:
        """Matches a glob pattern."""
        subdir = tmp_path / "dist"
        subdir.mkdir()
        target = subdir / "myapp.bin"
        target.write_text("binary", encoding="utf-8")

        result = match_candidate_path(tmp_path, "dist/*.bin")

        assert result == target

    def test_returns_none_for_no_match(self, tmp_path: Path) -> None:
        """Returns None when no file matches."""
        result = match_candidate_path(tmp_path, "nonexistent")
        assert result is None
