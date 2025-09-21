"""Tests for check_github_release.py."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from ._helpers import load_script_module


class DummyResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "DummyResponse":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: object | None) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


@pytest.fixture(name="module")
def fixture_module() -> Any:
    """Load the ``check_github_release`` script for testing.

    Returns
    -------
    Any
        Imported module object exposing the ``main`` entrypoint.
    """
    return load_script_module("check_github_release")


def test_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], module: Any) -> None:
    """Confirm that a published release prints a success message.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``urllib.request.urlopen``.
    capsys : pytest.CaptureFixture[str]
        Captures standard output and error from the command execution.
    module : Any
        Script module under test.
    """
    def fake_urlopen(request: Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": False, "prerelease": False, "name": "1.2.3"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    module.main(tag="v1.2.3", token="token", repo="owner/repo")

    captured = capsys.readouterr()
    assert "GitHub Release '1.2.3' is published." in captured.out


def test_draft_release(monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Fail when the release is still marked as a draft.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``urllib.request.urlopen``.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures emitted error output.
    """
    def fake_urlopen(request: Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": True, "prerelease": False, "name": "draft"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token="token", repo="owner/repo")

    captured = capsys.readouterr()
    assert "still a draft" in captured.err


def test_prerelease(monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Fail when the release is published as a prerelease.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``urllib.request.urlopen``.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures emitted error output.
    """
    def fake_urlopen(request: Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": False, "prerelease": True, "name": "pre"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token="token", repo="owner/repo")

    captured = capsys.readouterr()
    assert "prerelease" in captured.err


def test_missing_release(monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Raise an error when the requested release does not exist.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``urllib.request.urlopen``.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures emitted error output.
    """
    def fake_urlopen(request: Any, timeout: float = 30) -> Any:  # noqa: ANN401
        raise module.urllib.error.HTTPError(
            url=str(request.full_url),
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token="token", repo="owner/repo")

    captured = capsys.readouterr()
    assert "No GitHub release found" in captured.err


def test_permission_denied(monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Surface permission errors from the GitHub API.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace ``urllib.request.urlopen``.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures emitted error output.
    """
    detail = b"forbidden"
    error = module.urllib.error.HTTPError(
        url="https://api.github.com",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=io.BytesIO(detail),
    )

    def raising_urlopen(request: Any, timeout: float = 30) -> Any:  # noqa: ANN401
        raise error

    monkeypatch.setattr(module.urllib.request, "urlopen", raising_urlopen)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token="token", repo="owner/repo")

    captured = capsys.readouterr()
    assert "GitHub token lacks permission" in captured.err


def test_retries_then_success(monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Retry transient failures before succeeding.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to replace network calls and sleep behaviour.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures command output for assertions.
    """
    attempts: list[int] = []

    def fake_urlopen(request: Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        attempts.append(1)
        if len(attempts) < 3:
            raise module.urllib.error.URLError("temporary")
        return DummyResponse({"draft": False, "prerelease": False, "name": "ok"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.main(tag="v1.0.0", token="token", repo="owner/repo")

    assert len(attempts) == 3
    captured = capsys.readouterr()
    assert "GitHub Release 'ok' is published." in captured.out
