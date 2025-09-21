"""Tests for publish_release.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ._helpers import REPO_ROOT, load_script_module


@pytest.fixture(name="publish_module")
def fixture_publish_module() -> Any:
    """Load the ``publish_release`` script and adjust its import path.

    Returns
    -------
    Any
        Imported module with ``run_cmd`` exposed for monkeypatching.
    """
    module = load_script_module("publish_release")
    # Ensure cmd_utils is importable by mimicking script behaviour
    if str(REPO_ROOT) not in module.sys.path:  # type: ignore[attr-defined]
        module.sys.path.insert(0, str(REPO_ROOT))  # type: ignore[attr-defined]
    return module


def test_publish_default_index(monkeypatch: pytest.MonkeyPatch, publish_module: Any) -> None:
    """Use the default PyPI index when no custom index is provided.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``run_cmd`` during the test.
    publish_module : Any
        Script module under test.
    """
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="")

    assert calls == [["uv", "publish"]]


def test_publish_custom_index(monkeypatch: pytest.MonkeyPatch, publish_module: Any) -> None:
    """Invoke ``uv publish`` with the provided custom index.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``run_cmd`` during the test.
    publish_module : Any
        Script module under test.
    """
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="testpypi")

    assert calls == [["uv", "publish", "--index", "testpypi"]]


def test_publish_run_cmd_error(monkeypatch: pytest.MonkeyPatch, publish_module: Any) -> None:
    """Propagate exceptions raised by ``run_cmd``.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``run_cmd`` during the test.
    publish_module : Any
        Script module under test.
    """
    class DummyError(Exception):
        pass

    def fake_run_cmd(_: list[str], **__: object) -> None:
        raise DummyError("uv publish failed")

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    with pytest.raises(DummyError):
        publish_module.main(index="")
