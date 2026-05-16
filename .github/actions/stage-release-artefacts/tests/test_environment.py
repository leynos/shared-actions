"""Tests for stage-release-artefacts environment helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.environment import require_env_path


class TestRequireEnvPath:
    """Tests for the require_env_path function."""

    def test_returns_path_when_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Returns Path when environment variable is set."""
        monkeypatch.setenv("TEST_VAR", str(tmp_path))
        result = require_env_path("TEST_VAR")
        assert result == tmp_path

    def test_raises_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises StageError when environment variable is not set."""
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(StageError, match="not set"):
            require_env_path("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises StageError when environment variable is empty."""
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(StageError, match="not set"):
            require_env_path("TEST_VAR")
