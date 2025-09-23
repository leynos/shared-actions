"""Tests for check_github_release.py."""

from __future__ import annotations

import io
import json
import typing as typ
import uuid

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from types import ModuleType

from ._helpers import load_script_module


class DummyResponse:
    """In-memory substitute for an ``urllib`` HTTP response."""

    def __init__(self, payload: dict[str, typ.Any]) -> None:
        """Store the JSON payload returned by the fake response."""
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> DummyResponse:
        """Return the response instance for context manager usage."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Propagate exceptions raised within the context manager."""
        return

    def read(self) -> bytes:
        """Return the cached payload bytes."""
        return self._payload


@pytest.fixture(name="module")
def fixture_module() -> ModuleType:
    """Load the ``check_github_release`` script module under test."""
    return load_script_module("check_github_release")


@pytest.fixture(name="fake_token")
def fixture_fake_token() -> str:
    """Generate a unique but fake token for GitHub API requests."""
    return f"test-token-{uuid.uuid4().hex}"


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

    def fake_urlopen(request: typ.Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": False, "prerelease": False, "name": "1.2.3"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

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

    def fake_urlopen(request: typ.Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": True, "prerelease": False, "name": "draft"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

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

    def fake_urlopen(request: typ.Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        return DummyResponse({"draft": False, "prerelease": True, "name": "pre"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

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

    def fake_urlopen(request: typ.Any, timeout: float = 30) -> typ.Any:  # noqa: ANN401
        raise module.urllib.error.HTTPError(
            url=str(request.full_url),
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(module.typer.Exit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "No GitHub release found" in captured.err


def test_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Exit with a helpful error when GitHub responds with 403 Forbidden."""
    detail = b"forbidden"
    error = module.urllib.error.HTTPError(
        url="https://api.github.com",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=io.BytesIO(detail),
    )

    def raising_urlopen(request: typ.Any, timeout: float = 30) -> typ.Any:  # noqa: ANN401
        raise error

    monkeypatch.setattr(module.urllib.request, "urlopen", raising_urlopen)

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

    def fake_urlopen(request: typ.Any, timeout: float = 30) -> DummyResponse:  # noqa: ANN401
        attempts.append(1)
        if len(attempts) < 3:
            message = "temporary"
            raise module.urllib.error.URLError(message)
        return DummyResponse({"draft": False, "prerelease": False, "name": "ok"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
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

    def failing_urlopen(request: typ.Any, timeout: float = 30) -> typ.Any:  # noqa: ANN401
        _ = request, timeout
        raise module.urllib.error.URLError("temporary")

    monkeypatch.setattr(module.urllib.request, "urlopen", failing_urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    with pytest.raises(module.typer.Exit) as exc_info:
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert exc_info.value.exit_code == 1
    assert "temporary" in captured.err or "fetch" in captured.err
