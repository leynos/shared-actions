"""Tests for check_github_release.py."""

from __future__ import annotations

import typing as typ
import uuid

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

from ._helpers import load_script_module


@pytest.fixture(name="module")
def fixture_module() -> ModuleType:
    """Load the ``check_github_release`` script module under test."""
    return load_script_module("check_github_release")


@pytest.fixture(name="fake_token")
def fixture_fake_token() -> str:
    """Generate a unique but fake token for GitHub API requests."""
    return f"test-token-{uuid.uuid4().hex}"


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    handler: typ.Callable[[typ.Any], typ.Any],
) -> None:
    """Replace the retry transport with a handler backed by ``MockTransport``."""

    def factory() -> module.RetryTransport:
        transport = module.httpx.MockTransport(handler)
        return module.RetryTransport(transport=transport, retry=module._GithubRetry())

    monkeypatch.setattr(module, "_build_retry_transport", factory)


def test_sleep_with_jitter_allows_custom_rng(module: ModuleType) -> None:
    """Allow tests to provide deterministic jitter and sleep functions."""
    calls: list[float] = []

    class FixedRandom:
        """Stub RNG that always returns a fixed jitter fraction."""

        def uniform(self, a: float, b: float) -> float:
            assert a == 0.0
            assert b == 0.1
            return 0.05

    module._sleep_with_jitter(4.0, jitter=FixedRandom(), sleep=calls.append)

    assert calls == [4.2]


def test_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: ModuleType,
    fake_token: str,
) -> None:
    """Print a success message when GitHub marks the release as published."""

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {fake_token}"
        payload = {"draft": False, "prerelease": False, "name": "1.2.3"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_transport(monkeypatch, module, handler)

    module.main(tag="v1.2.3", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "GitHub Release '1.2.3' is published." in captured.out


def test_draft_release(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Exit with an error when GitHub reports the release as a draft."""

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        payload = {"draft": True, "prerelease": False, "name": "draft"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_transport(monkeypatch, module, handler)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "still a draft" in captured.err


def test_prerelease(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Exit with an error when GitHub flags the release as a prerelease."""

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        payload = {"draft": False, "prerelease": True, "name": "pre"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_transport(monkeypatch, module, handler)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "prerelease" in captured.err


def test_missing_release(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Raise an error when the GitHub API cannot find the release."""

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(404, content=b"", request=request)

    _install_transport(monkeypatch, module, handler)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "No GitHub release found" in captured.err


def test_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Exit with guidance when GitHub rejects the authentication token."""
    detail = b"Bad credentials"

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(401, content=detail, request=request)

    _install_transport(monkeypatch, module, handler)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "Verify that GH_TOKEN" in captured.err
    assert "Unauthorized" in captured.err


def test_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Exit with a helpful error when GitHub responds with 403 Forbidden."""
    detail = b"forbidden"

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(403, content=detail, request=request)

    _install_transport(monkeypatch, module, handler)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "GitHub token lacks permission" in captured.err


def test_retries_then_success(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Retry transient HTTP failures until GitHub releases the metadata."""
    attempts: list[int] = []

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            message = "temporary"
            raise module.httpx.ReadTimeout(message, request=request)
        payload = {"draft": False, "prerelease": False, "name": "ok"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_transport(monkeypatch, module, handler)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    assert len(attempts) == 3
    captured = capsys.readouterr()
    assert "GitHub Release 'ok' is published." in captured.out


def test_retries_then_fail(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Abort after exhausting retries when transient errors persist."""

    def handler(request: module.httpx.Request) -> module.httpx.Response:
        message = "temporary"
        raise module.httpx.ReadTimeout(message, request=request)

    _install_transport(monkeypatch, module, handler)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    with pytest.raises(module.typer.Exit) as exc_info:
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert exc_info.value.exit_code == 1
    assert "temporary" in captured.err or "fetch" in captured.err
