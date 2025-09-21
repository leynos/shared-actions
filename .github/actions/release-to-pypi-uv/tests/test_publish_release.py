"""Tests for publish_release.py."""

from __future__ import annotations

import typing as typ
from pathlib import Path
from types import ModuleType

import pytest

from ._helpers import REPO_ROOT, load_script_module


@pytest.fixture(name="publish_module")
def fixture_publish_module() -> ModuleType:
    """Load the ``publish_release`` script module and ensure import paths.

    Returns
    -------
    ModuleType
        Imported script module under test.
    """

    module = load_script_module("publish_release")
    if str(REPO_ROOT) not in module.sys.path:  # type: ignore[attr-defined]
        module.sys.path.insert(0, str(REPO_ROOT))  # type: ignore[attr-defined]
    return module


def test_publish_default_index(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Invoke ``uv publish`` without an index when none is provided.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to stub the ``run_cmd`` helper.
    publish_module : ModuleType
        Loaded ``publish_release`` script module under test.
    """

    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="")

    assert calls == [["uv", "publish"]]


def test_publish_custom_index(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Add the ``--index`` flag when a custom index value is supplied.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to stub the ``run_cmd`` helper.
    publish_module : ModuleType
        Loaded ``publish_release`` script module under test.
    """

    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="testpypi")

    assert calls == [["uv", "publish", "--index", "testpypi"]]


def test_publish_run_cmd_error(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Propagate errors raised by ``run_cmd`` during publishing.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to stub the ``run_cmd`` helper.
    publish_module : ModuleType
        Loaded ``publish_release`` script module under test.
    """

    class DummyError(Exception):
        pass

    def fake_run_cmd(_: list[str], **__: object) -> None:
        raise DummyError("uv publish failed")

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    with pytest.raises(DummyError):
        publish_module.main(index="")
