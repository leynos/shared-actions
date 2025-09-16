"""Tests for the :mod:`cmd_utils` helper."""

from __future__ import annotations

import subprocess
import sys

import pytest
from plumbum import local

from cmd_utils import run_cmd


def test_run_cmd_foreground_timeout() -> None:
    """Foreground commands honour the configured timeout."""
    sleeper = local[sys.executable]["-c", "import time; time.sleep(5)"]
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd(sleeper, fg=True, timeout=0.2)


def test_run_cmd_foreground_success() -> None:
    """Foreground commands still succeed without a timeout."""
    cmd = local[sys.executable]["-c", "print('ok')"]
    assert run_cmd(cmd, fg=True) == "ok\n"
