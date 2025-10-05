"""Bridge to the repository-level :mod:`cmd_utils` utilities."""

from __future__ import annotations

from cmd_utils_importer import import_cmd_utils

run_cmd = import_cmd_utils().run_cmd

__all__ = ["run_cmd"]
