"""Tests for write_summary.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ._helpers import load_script_module


@pytest.fixture(name="write_module")
def fixture_write_module() -> Any:
    """Load the ``write_summary`` script for testing.

    Returns
    -------
    Any
        Imported module object exposing the ``main`` entrypoint.
    """
    return load_script_module("write_summary")


def test_write_summary_appends_markdown(tmp_path: Path, write_module: Any) -> None:
    """Append a new summary when the file is initially empty.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory containing the summary file.
    write_module : Any
        Script module under test.
    """
    summary_path = tmp_path / "summary.md"

    write_module.main(
        tag="v1.2.3",
        index="",
        environment_name="pypi",
        summary_path=summary_path,
    )

    content = summary_path.read_text(encoding="utf-8")
    assert "## Release summary" in content
    assert "- Released tag: v1.2.3" in content
    assert "- Publish index: pypi (default)" in content


def test_write_summary_handles_existing_content(tmp_path: Path, write_module: Any) -> None:
    """Preserve existing summary content while appending new entries.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory containing the summary file.
    write_module : Any
        Script module under test.
    """
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("Existing\n", encoding="utf-8")

    write_module.main(
        tag="v1.2.3",
        index="custom",
        environment_name="prod",
        summary_path=summary_path,
    )

    content = summary_path.read_text(encoding="utf-8")
    assert content.endswith("- Environment: prod\n")
    assert content.count("## Release summary") == 1


def test_write_summary_raises_on_io_error(write_module: Any) -> None:
    """Propagate I/O errors encountered when writing the summary file.

    Parameters
    ----------
    write_module : Any
        Script module under test.
    """
    summary_path = Path("/nonexistent/path/summary.md")

    with pytest.raises(OSError):
        write_module.main(
            tag="v1.0.0",
            index="",
            environment_name="pypi",
            summary_path=summary_path,
        )
