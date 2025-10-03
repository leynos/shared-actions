"""Tests for architecture mapping helpers."""

from __future__ import annotations

import pytest

from scripts.validate_architecture import (
    UnsupportedTargetError,
    deb_arch_for_target,
    nfpm_arch_for_target,
)


def test_nfpm_arch_for_target_returns_alias() -> None:
    """nfpm_arch_for_target resolves canonical GOARCH labels."""
    result = nfpm_arch_for_target("x86_64-unknown-linux-gnu")

    assert result == "amd64"


def test_deb_arch_for_target_returns_expected() -> None:
    """deb_arch_for_target maps triples onto Debian architectures."""
    result = deb_arch_for_target("aarch64-unknown-linux-gnu")

    assert result == "arm64"


def test_architecture_helpers_raise_for_unknown_target() -> None:
    """Unknown targets trigger UnsupportedTargetError."""
    with pytest.raises(UnsupportedTargetError):
        nfpm_arch_for_target("mips64-unknown-linux-gnu")
