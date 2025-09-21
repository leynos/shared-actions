"""Tests for write_summary.py."""

from __future__ import annotations

import typing as typ
from pathlib import Path
from types import ModuleType

import pytest

from ._helpers import load_script_module


@pytest.fixture(name="write_module")
def fixture_write_module() -> ModuleType:
    return load_script_module("write_summary")


def test_write_summary_appends_markdown(tmp_path: Path, write_module: ModuleType) -> None:
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


def test_write_summary_handles_existing_content(
    tmp_path: Path, write_module: ModuleType
) -> None:
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


def test_write_summary_raises_on_io_error(write_module: ModuleType) -> None:
    summary_path = Path("/nonexistent/path/summary.md")

    with pytest.raises(OSError):
        write_module.main(
            tag="v1.0.0",
            index="",
            environment_name="pypi",
            summary_path=summary_path,
        )
