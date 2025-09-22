"""Tests for publish_release.py."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

import pytest
from typer.testing import CliRunner

from ._helpers import REPO_ROOT, load_script_module


@pytest.fixture(name="publish_module")
def fixture_publish_module() -> ModuleType:
    """Load the ``publish_release`` script module with repository paths set."""
    module = load_script_module("publish_release")
    if str(REPO_ROOT) not in module.sys.path:  # type: ignore[attr-defined]
        module.sys.path.insert(0, str(REPO_ROOT))  # type: ignore[attr-defined]
    return module


def test_publish_default_index(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    publish_module: ModuleType,
) -> None:
    """Invoke ``uv publish`` without an index when none is provided."""
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="")

    assert calls == [["uv", "publish"]]
    captured = capsys.readouterr()
    assert "Publishing with uv to default index (PyPI)" in captured.out


def test_publish_custom_index(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    publish_module: ModuleType,
) -> None:
    """Add the ``--index`` flag when a custom index value is supplied."""
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    publish_module.main(index="  testpypi  ")

    assert calls == [["uv", "publish", "--index", "testpypi"]]
    captured = capsys.readouterr()
    assert "Publishing with uv to index 'testpypi'" in captured.out


def test_publish_run_cmd_error(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Propagate errors raised by ``run_cmd`` during publishing."""

    class DummyError(Exception):
        pass

    def fake_run_cmd(_: list[str], **__: object) -> None:
        message = "uv publish failed"
        raise DummyError(message)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    with pytest.raises(DummyError):
        publish_module.main(index="")


def test_cli_proxies_to_main(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Ensure the CLI entrypoint forwards arguments to ``main``."""
    received: dict[str, str] = {}

    def fake_main(*, index: str) -> None:
        received["index"] = index

    monkeypatch.setattr(publish_module, "main", fake_main)

    publish_module.cli(index="mirror")

    assert received == {"index": "mirror"}


def test_cli_runner_default_index(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Exercise the CLI behaviour when no index is provided."""
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    runner = CliRunner()
    app = publish_module.typer.Typer()
    app.command()(publish_module.cli)
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert calls == [["uv", "publish"]]
    assert "Publishing with uv to default index (PyPI)" in result.output


def test_cli_runner_respects_env_index(
    monkeypatch: pytest.MonkeyPatch, publish_module: ModuleType
) -> None:
    """Accept the index from the GitHub Action input environment variable."""
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], **_: object) -> None:
        calls.append(args)

    monkeypatch.setattr(publish_module, "run_cmd", fake_run_cmd)

    runner = CliRunner()
    app = publish_module.typer.Typer()
    app.command()(publish_module.cli)
    result = runner.invoke(app, [], env={"INPUT_UV_INDEX": "testpypi"})

    assert result.exit_code == 0
    assert calls == [["uv", "publish", "--index", "testpypi"]]
    assert "Publishing with uv to index 'testpypi'" in result.output
