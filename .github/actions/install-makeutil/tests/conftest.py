"""Make the install-makeutil scripts importable, and run them on POSIX only.

The scripts live in a sibling ``scripts/`` directory rather than in
``tests/`` itself, so pytest's default import-mode discovery — which adds
only a test file's own directory to ``sys.path`` — never finds them. This
mirrors the ``--extra-search-path`` entries the ``typecheck`` Makefile target
adds for the same directory.

The action installs a Linux binary and runs only on Linux runners, so its
paths are POSIX paths. On Windows the tests would check POSIX path rules
against Windows drive-letter temporary directories, so they are not collected there.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Linux-only action: see the module docstring.
collect_ignore_glob = ["test_*.py"] if sys.platform == "win32" else []
