"""Tests for write_summary.py."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from ._helpers import load_script_module


@pytest.fixture(name="write_module")
<<<<<<< HEAD
def fixture_write_module() -> Any:
    """Load the ``write_summary`` script for testing.

    Returns
    -------
    Any
        Imported module object exposing the ``main`` entrypoint.
    """
||||||| parent of 0847f81 (Silence type-check import lints for release action)
def fixture_write_module() -> Any:
=======
def fixture_write_module() -> typ.Any:
>>>>>>> 0847f81 (Silence type-check import lints for release action)
    return load_script_module("write_summary")


<<<<<<< HEAD
def test_write_summary_appends_markdown(tmp_path: Path, write_module: Any) -> None:
    """Append a new summary when the file is initially empty.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory containing the summary file.
    write_module : Any
        Script module under test.
    """
||||||| parent of 0847f81 (Silence type-check import lints for release action)
def test_write_summary_appends_markdown(tmp_path: Path, write_module: Any) -> None:
=======
def test_write_summary_appends_markdown(
    tmp_path: Path, write_module: typ.Any
) -> None:
>>>>>>> 0847f81 (Silence type-check import lints for release action)
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


<<<<<<< HEAD
def test_write_summary_handles_existing_content(tmp_path: Path, write_module: Any) -> None:
    """Preserve existing summary content while appending new entries.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory containing the summary file.
    write_module : Any
        Script module under test.
    """
||||||| parent of 0847f81 (Silence type-check import lints for release action)
def test_write_summary_handles_existing_content(tmp_path: Path, write_module: Any) -> None:
=======
def test_write_summary_handles_existing_content(
    tmp_path: Path, write_module: typ.Any
) -> None:
>>>>>>> 0847f81 (Silence type-check import lints for release action)
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


<<<<<<< HEAD
def test_write_summary_raises_on_io_error(write_module: Any) -> None:
    """Propagate I/O errors encountered when writing the summary file.

    Parameters
    ----------
    write_module : Any
        Script module under test.
    """
||||||| parent of 0847f81 (Silence type-check import lints for release action)
def test_write_summary_raises_on_io_error(write_module: Any) -> None:
=======
def test_write_summary_raises_on_io_error(write_module: typ.Any) -> None:
>>>>>>> 0847f81 (Silence type-check import lints for release action)
    summary_path = Path("/nonexistent/path/summary.md")

    with pytest.raises(OSError):
        write_module.main(
            tag="v1.0.0",
            index="",
            environment_name="pypi",
            summary_path=summary_path,
        )
