"""Make ``uv_gate`` importable from the tests, and run the black-box tests on POSIX.

``uv_gate.py`` sits beside this directory rather than inside it, so pytest's
default import-mode discovery never finds it. The black-box tests run the
helper against a fake ``uv`` executable written as a ``#!`` script, which
Windows cannot execute; the Windows behaviour is covered by the pilot
repository's Windows legs instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

_GATE_DIR = Path(__file__).resolve().parents[1]
if str(_GATE_DIR) not in sys.path:
    sys.path.insert(0, str(_GATE_DIR))

collect_ignore_glob = ["test_blackbox*.py"] if sys.platform == "win32" else []
