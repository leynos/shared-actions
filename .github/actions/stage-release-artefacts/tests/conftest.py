"""Shared pytest fixtures for stage-release-artefacts tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)


@pytest.fixture(scope="session", autouse=True)
def _stage_release_artefacts_scripts_on_syspath() -> None:
    """Ensure stage-release-artefacts scripts are importable by all tests."""
    prepend_to_syspath(SCRIPTS_DIR)


@pytest.fixture
def bdd_context(tmp_path: Path) -> dict[str, object]:
    """Return mutable context for pytest-bdd steps."""
    return {"workspace": tmp_path / "workspace", "target": ""}
