"""Make the install-makeutil scripts importable by their bare module names.

The scripts live in a sibling ``scripts/`` directory rather than in
``tests/`` itself, so pytest's default import-mode discovery — which adds
only a test file's own directory to ``sys.path`` — never finds them. This
mirrors the ``--extra-search-path`` entries the ``typecheck`` Makefile target
adds for the same directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
