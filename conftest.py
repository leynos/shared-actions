"""Pytest configuration for shared actions tests."""

from __future__ import annotations

import sys

import pytest


if sys.platform != "win32":  # pragma: win32 no cover - Windows lacks cmd-mox support
    pytest_plugins = ("cmd_mox.pytest_plugin",)
else:

    @pytest.fixture()
    def cmd_mox():  # pragma: win32 no cover - fixture only used on Windows
        """Skip tests that rely on cmd-mox on Windows."""

        pytest.skip("cmd-mox does not support Windows")
