"""The estate's CV-005 CodeScene coverage and `codescene` environment contracts.

Main owns CodeScene: one push-to-main publisher uploads coverage and writes
the ratchet baseline, nothing a pull request can start talks to CodeScene or
holds its credential, and only the uploading job may declare the `codescene`
environment. Repositories run these rules through `cv005-contracts check`
rather than keeping their own copies.

The readers are pure over supplied text or parsed documents, apart from
`loading.read_workflows` and `actions.read_actions`, which are the
filesystem boundary.
"""

from __future__ import annotations

from .api import (
    ContractError,
    Violation,
    assert_environment_contract,
    assert_publisher_contract,
    violations,
)
from .config import Config, ConfigError, load_config

__all__ = [
    "Config",
    "ConfigError",
    "ContractError",
    "Violation",
    "assert_environment_contract",
    "assert_publisher_contract",
    "load_config",
    "violations",
]
