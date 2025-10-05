"""Tests for the :mod:`cmd_utils` helper."""

from __future__ import annotations

import sys

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessTimedOut

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd


def test_run_cmd_foreground_timeout() -> None:
    """Foreground commands honour the configured timeout."""
    sleeper = local[sys.executable]["-c", "import time; time.sleep(5)"]
    with pytest.raises(ProcessTimedOut):
        run_cmd(sleeper, method="run_fg", timeout=0.2)


def test_run_cmd_foreground_success() -> None:
    """Foreground commands still succeed without a timeout."""
    cmd = local[sys.executable]["-c", "print('ok')"]
    run_cmd(cmd, method="run_fg")
