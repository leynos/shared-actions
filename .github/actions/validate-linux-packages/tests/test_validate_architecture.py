"""Tests for architecture mapping helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_architecture.py"


@pytest.fixture(scope="module")
def validate_architecture_module() -> object:
    """Load the validate_architecture module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_architecture")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_architecture", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("unable to load validate_architecture module")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_nfpm_arch_for_target_returns_alias(
    validate_architecture_module: object,
) -> None:
    """nfpm_arch_for_target resolves canonical GOARCH labels."""
    result = validate_architecture_module.nfpm_arch_for_target(
        "x86_64-unknown-linux-gnu"
    )

    assert result == "amd64"


def test_deb_arch_for_target_returns_expected(
    validate_architecture_module: object,
) -> None:
    """deb_arch_for_target maps triples onto Debian architectures."""
    result = validate_architecture_module.deb_arch_for_target("aarch64-unknown-linux-gnu")

    assert result == "arm64"


def test_architecture_helpers_raise_for_unknown_target(
    validate_architecture_module: object,
) -> None:
    """Unknown targets trigger UnsupportedTargetError."""
    with pytest.raises(validate_architecture_module.UnsupportedTargetError):
        validate_architecture_module.nfpm_arch_for_target("mips64-unknown-linux-gnu")
