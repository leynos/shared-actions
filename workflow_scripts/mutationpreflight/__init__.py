"""The mutation environment preflight, split into one module per phase.

``workflow_scripts/mutation_preflight.py`` is the entry point: it keeps the
PEP 723 header that makes the helper runnable with ``uv run --script`` and
re-exports what this package offers. Splitting the phases out keeps each one
small enough to read on its own.
"""

from __future__ import annotations

from .cli import app, main

__all__ = ["app", "main"]
