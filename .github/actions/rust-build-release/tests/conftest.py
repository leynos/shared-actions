"""Shared pytest fixtures for rust-build-release tests."""

from __future__ import annotations

import collections.abc as cabc

import pytest


@pytest.fixture
def uncapture_if_verbose(
    request: pytest.FixtureRequest, capfd: pytest.CaptureFixture[str]
) -> cabc.Iterator[None]:
    """Disable output capture when pytest runs with ``-v`` or higher verbosity."""
    if request.config.get_verbosity() > 0:
        with capfd.disabled():
            yield
    else:
        yield
