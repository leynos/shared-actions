"""Staging helper package for release artefacts.

This package provides utilities for staging release artefacts according to a
TOML configuration file. It handles path resolution, checksum generation,
and GitHub Actions output writing.
"""

from __future__ import annotations

from .config import ArtefactConfig, StagingConfig, load_config
from .environment import require_env_path
from .errors import StageError
from .pipeline import StageResult, stage_artefacts

__all__ = [
    "ArtefactConfig",
    "StageError",
    "StageResult",
    "StagingConfig",
    "load_config",
    "require_env_path",
    "stage_artefacts",
]
