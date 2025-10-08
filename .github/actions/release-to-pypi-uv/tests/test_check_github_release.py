"""Tests for check_github_release.py."""

from __future__ import annotations

import typing as typ
import uuid

import httpx
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


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    handler: typ.Callable[[int, httpx.Request], httpx.Response | Exception],
    *,
    record_sleep: bool = False,
) -> list[int] | tuple[list[int], list[float]]:
    """Replace ``httpx.Client`` with a stub that delegates to ``handler``."""
    attempts: list[int] = []
    sleep_calls: list[float] = []

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._headers = kwargs.get("headers", {})

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> bool:
            return False

        def get(self, url: httpx.URL, *, follow_redirects: bool) -> httpx.Response:
            assert follow_redirects is False
            request = httpx.Request("GET", url, headers=self._headers)
            attempt = len(attempts) + 1
            attempts.append(attempt)
            result = handler(attempt, request)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(module.httpx, "Client", FakeClient)

    def fake_sleep(duration: float) -> None:
        if record_sleep:
            sleep_calls.append(duration)

    monkeypatch.setattr(
        module._fetch_release_with_retry.retry,
        "sleep",
        fake_sleep if record_sleep else lambda _: None,
    )

    if record_sleep:
        return attempts, sleep_calls
    return attempts


def test_retry_wait_strategy_progression(module: ModuleType) -> None:
    """Ensure the retry wait strategy scales delays with jitter bounds."""
    wait = module._retry_wait_strategy()

    # Force deterministic jitter
    class FixedRandom:
        def __init__(self) -> None:
            self.values = iter([0.0, 0.5, 1.0])

        def uniform(self, a: float, b: float) -> float:
            return next(self.values)

    wait._rng = FixedRandom()  # type: ignore[attr-defined]
    delays = [wait(type("State", (), {"attempt_number": 1})())]
    delays.append(wait(type("State", (), {"attempt_number": 2})()))
    delays.append(wait(type("State", (), {"attempt_number": 3})()))
    delays.append(wait(type("State", (), {"attempt_number": 4})()))
    assert delays[0] == 0.0
    assert pytest.approx(delays[1]) == module._INITIAL_DELAY
    assert delays[2] > delays[1]
    assert delays[3] >= delays[2]


def test_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: ModuleType,
    fake_token: str,
) -> None:
    """Print a success message when GitHub marks the release as published."""

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {fake_token}"
        payload = {"draft": False, "prerelease": False, "name": "1.2.3"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_client(monkeypatch, module, handler)

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

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        payload = {"draft": True, "prerelease": False, "name": "draft"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
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

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        payload = {"draft": False, "prerelease": True, "name": "pre"}
        return module.httpx.Response(200, json=payload, request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
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

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(404, content=b"", request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
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

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(401, content=detail, request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
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

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(403, content=detail, request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "GitHub token lacks permission" in captured.err


def test_invalid_json_reports_payload(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Surface the problematic payload when JSON decoding fails."""
    payload = "{" * (module._JSON_PAYLOAD_PREVIEW_LIMIT + 10)

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(200, content=payload.encode(), request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert "invalid JSON" in captured.err
    preview = payload[: module._JSON_PAYLOAD_PREVIEW_LIMIT]
    assert preview in captured.err
    assert "..." in captured.err


def test_forbidden_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Respect Retry-After guidance on 403 responses before retrying."""

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        if attempt == 1:
            return module.httpx.Response(
                403,
                headers={"Retry-After": "1"},
                request=request,
                content=b"",
            )
        payload = {"draft": False, "prerelease": False, "name": "ok"}
        return module.httpx.Response(200, json=payload, request=request)

    _, sleep_calls = _install_client(monkeypatch, module, handler, record_sleep=True)

    module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    assert sleep_calls == [1.0]
    captured = capsys.readouterr()
    assert "GitHub Release 'ok' is published." in captured.out


def test_retries_then_success(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Retry transient HTTP failures until GitHub releases the metadata."""

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        if attempt < 3:
            message = "temporary"
            raise module.httpx.ReadTimeout(message, request=request)
        payload = {"draft": False, "prerelease": False, "name": "ok"}
        return module.httpx.Response(200, json=payload, request=request)

    attempts = _install_client(monkeypatch, module, handler)

    module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    assert attempts == [1, 2, 3]
    captured = capsys.readouterr()
    assert "GitHub Release 'ok' is published." in captured.out


def test_error_detail_truncated(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Truncate oversized error responses before surfacing them."""
    detail = "x" * (module._ERROR_DETAIL_LIMIT + 50)

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        return module.httpx.Response(500, content=detail.encode(), request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit):
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    truncated = detail[: module._ERROR_DETAIL_LIMIT] + "…"
    assert truncated in captured.err
    assert detail not in captured.err


def test_retries_then_fail(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    fake_token: str,
) -> None:
    """Abort after exhausting retries when transient errors persist."""

    def handler(attempt: int, request: module.httpx.Request) -> module.httpx.Response:
        message = "temporary"
        raise module.httpx.ReadTimeout(message, request=request)

    _install_client(monkeypatch, module, handler)

    with pytest.raises(SystemExit) as exc_info:
        module.main(tag="v1.0.0", token=fake_token, repo="owner/repo")

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Failed to reach GitHub API" in captured.err
    assert "temporary" in captured.err
