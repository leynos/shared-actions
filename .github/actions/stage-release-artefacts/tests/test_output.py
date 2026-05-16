"""Tests for stage-release-artefacts workflow outputs."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.output import (
    prepare_output_data,
    validate_no_reserved_key_collisions,
    write_github_output,
)


class TestPrepareOutputData:
    """Tests for the prepare_output_data function."""

    def test_includes_all_fields(self, tmp_path: Path) -> None:
        """Output data includes all required fields."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        staged_file = staging_dir / "myapp"
        staged_file.touch()

        result = prepare_output_data(
            staging_dir=staging_dir,
            staged_paths=[staged_file],
            outputs={"binary_path": staged_file},
            checksums={"myapp": "abc123"},
        )

        assert "artifact_dir" in result
        assert "dist_dir" in result
        assert "staged_files" in result
        assert "artefact_map" in result
        assert "checksum_map" in result
        assert "binary_path" in result
        assert result["powershell_help_dir"] == ""


class TestValidateNoReservedKeyCollisions:
    """Tests for the validate_no_reserved_key_collisions function."""

    def test_allows_non_reserved_keys(self, tmp_path: Path) -> None:
        """Non-reserved keys are allowed."""
        outputs = {"binary_path": tmp_path / "myapp"}
        validate_no_reserved_key_collisions(outputs)  # Should not raise

    def test_raises_for_reserved_key(self, tmp_path: Path) -> None:
        """Reserved keys raise StageError."""
        outputs = {"powershell_help_dir": tmp_path / "myapp"}
        with pytest.raises(StageError, match="reserved keys"):
            validate_no_reserved_key_collisions(outputs)


class TestWriteGithubOutput:
    """Tests for the write_github_output function."""

    def test_writes_simple_values(self, tmp_path: Path) -> None:
        """Simple string values are written correctly."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"key": "value"})

        contents = output_file.read_text(encoding="utf-8")
        assert "key=value" in contents

    def test_escapes_special_characters(self, tmp_path: Path) -> None:
        """Special characters are escaped."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"key": "line1\nline2"})

        contents = output_file.read_text(encoding="utf-8")
        assert "key=line1%0Aline2" in contents

    def test_normalizes_windows_paths(self, tmp_path: Path) -> None:
        """Windows paths are normalized when flag is set."""
        output_file = tmp_path / "output"
        write_github_output(
            output_file,
            {"path": "C:\\Users\\test"},
            normalize_windows_paths=True,
        )

        contents = output_file.read_text(encoding="utf-8")
        assert "path=C:/Users/test" in contents

    def test_writes_list_values_with_heredoc(self, tmp_path: Path) -> None:
        """List values are written using heredoc syntax."""
        output_file = tmp_path / "output"
        write_github_output(output_file, {"staged_files": ["file1.txt", "file2.txt"]})

        contents = output_file.read_text(encoding="utf-8")
        assert "staged_files<<gh_STAGED_FILES" in contents
        assert "file1.txt\nfile2.txt" in contents
        assert "gh_STAGED_FILES\n" in contents

    def test_list_values_preserve_windows_paths(self, tmp_path: Path) -> None:
        """List values are not affected by normalize_windows_paths flag."""
        output_file = tmp_path / "output"
        write_github_output(
            output_file,
            {"files": ["C:\\Users\\file1.txt", "C:\\Users\\file2.txt"]},
            normalize_windows_paths=True,
        )

        contents = output_file.read_text(encoding="utf-8")
        # List values use heredoc syntax, not the scalar formatting
        assert "C:\\Users\\file1.txt" in contents
        assert "C:\\Users\\file2.txt" in contents

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created if needed."""
        output_file = tmp_path / "subdir" / "nested" / "output"
        write_github_output(output_file, {"key": "value"})

        assert output_file.exists()
        contents = output_file.read_text(encoding="utf-8")
        assert "key=value" in contents
