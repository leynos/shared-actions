#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "cyclopts>=2.9,<3.0",
#     "httpx>=0.28,<0.29",
#     "tenacity>=8.2,<9.0",
# ]
# ///
"""Verify that the GitHub Release for the provided tag exists and is published."""

from __future__ import annotations

import contextlib
import datetime as dt
import random
import sys
import typing as typ
from email.utils import parsedate_to_datetime

import cyclopts
import httpx
from cyclopts import App, Parameter
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_base

app = App(config=cyclopts.config.Env(prefix="", command=False))


class _UniformGenerator(typ.Protocol):
    """Protocol describing RNG objects that provide ``uniform``."""

    def uniform(self, a: float, b: float) -> float:
        """Return a random floating point number N such that ``a <= N <= b``."""


_JITTER = random.SystemRandom()
_JITTER_FACTOR = 0.1
_MAX_ATTEMPTS = 5
_BACKOFF_FACTOR = 1.5
_INITIAL_DELAY = 1.0
_MAX_BACKOFF_WAIT = 120.0
_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504, 429})
_ERROR_DETAIL_LIMIT = 1024
_JSON_PAYLOAD_PREVIEW_LIMIT = 500


class GithubReleaseError(RuntimeError):
    """Raised when the GitHub release is not ready for publishing."""


class GithubReleaseRetryError(GithubReleaseError):
    """Raised to indicate that the request should be retried."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _GithubRetryWait(wait_base):
    """Wait strategy that mirrors the action's backoff with jitter."""

    def __init__(
        self,
        *,
        initial_delay: float,
        backoff_factor: float,
        max_delay: float,
        jitter_factor: float,
        rng: _UniformGenerator | None = None,
    ) -> None:
        super().__init__()
        self._initial_delay = max(initial_delay, 0.0)
        self._backoff_factor = max(backoff_factor, 1.0)
        self._max_delay = max(max_delay, 0.0)
        self._jitter_factor = max(jitter_factor, 0.0)
        self._rng = _JITTER if rng is None else rng

    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = getattr(retry_state, "outcome", None)
        if outcome is not None and getattr(outcome, "failed", False):
            exception = outcome.exception()
            if (
                isinstance(exception, GithubReleaseRetryError)
                and exception.retry_after is not None
            ):
                return min(max(exception.retry_after, 0.0), self._max_delay)
        attempt_number = retry_state.attempt_number
        if attempt_number <= 1:
            return 0.0
        exponent = max(attempt_number - 2, 0)
        delay = min(
            self._initial_delay * (self._backoff_factor**exponent),
            self._max_delay,
        )
        if delay <= 0:
            return 0.0
        if self._jitter_factor <= 0:
            return delay
        jitter = delay * self._rng.uniform(0.0, self._jitter_factor)
        return min(delay + jitter, self._max_delay)


def _retry_wait_strategy() -> wait_base:
    return _GithubRetryWait(
        initial_delay=_INITIAL_DELAY,
        backoff_factor=_BACKOFF_FACTOR,
        max_delay=_MAX_BACKOFF_WAIT,
        jitter_factor=_JITTER_FACTOR,
    )


def _truncate_text(value: str, limit: int, *, suffix: str = "…") -> str:
    """Return ``value`` truncated to ``limit`` characters with ``suffix``."""
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + suffix


def _extract_error_detail(
    response: httpx.Response, *, limit: int = _ERROR_DETAIL_LIMIT
) -> str:
    """Return a truncated error detail string for logging and exceptions."""
    try:
        text = response.text
    except httpx.StreamError:  # pragma: no cover - unexpected streaming failure
        text = ""
    detail = text.strip() or response.reason_phrase or ""
    return _truncate_text(detail, limit)


def _parse_retry_after_header(value: str | None) -> float | None:
    """Return a parsed ``Retry-After`` delay in seconds when available."""
    if value is None:
        return None
    retry_after = value.strip()
    if not retry_after:
        return None
    if retry_after.isdigit():
        seconds = int(retry_after, base=10)
        if seconds <= 0:
            return None
        return min(float(seconds), _MAX_BACKOFF_WAIT)
    with contextlib.suppress((TypeError, ValueError, OverflowError)):
        parsed = parsedate_to_datetime(retry_after)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        now = dt.datetime.now(dt.UTC)
        delay = (parsed - now).total_seconds()
        if delay > 0:
            return min(delay, _MAX_BACKOFF_WAIT)
    return None


def _handle_http_response_error(response: httpx.Response, tag: str) -> typ.NoReturn:
    status = response.status_code
    detail = _extract_error_detail(response)

    if status == httpx.codes.UNAUTHORIZED:
        context = detail
        message = (
            "GitHub rejected the token (401 Unauthorized). "
            "Verify that GH_TOKEN is correct and has not expired."
        )
        if context:
            message = f"{message} ({context})"
        raise GithubReleaseError(message)

    if status == httpx.codes.FORBIDDEN:
        retry_after = _parse_retry_after_header(response.headers.get("Retry-After"))
        if retry_after is not None:
            reason = detail or "Forbidden response with Retry-After header"
            message = f"GitHub API request failed with status {status}: {reason}"
            raise GithubReleaseRetryError(message, retry_after=retry_after)
        permission_message = (
            "GitHub token lacks permission to read releases "
            "or has expired. "
            "Use a token with contents:read scope."
        )
        context = detail
        message = f"{permission_message} ({context})"
        raise GithubReleaseError(message)

    if status == httpx.codes.NOT_FOUND:
        message = (
            "No GitHub release found for tag "
            f"{tag}. Create and publish the release first."
        )
        raise GithubReleaseError(message)

    if status in _RETRYABLE_STATUS_CODES:
        retry_after = _parse_retry_after_header(response.headers.get("Retry-After"))
        failure_reason = detail or "Retryable error"
        message = f"GitHub API request failed with status {status}: {failure_reason}"
        raise GithubReleaseRetryError(message, retry_after=retry_after)

    failure_reason = detail or "Unknown error"
    message = f"GitHub API request failed with status {status}: {failure_reason}"
    raise GithubReleaseError(message)


def _request_release(repo: str, tag: str, token: str) -> dict[str, object]:
    url = httpx.URL(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    if url.scheme != "https":  # pragma: no cover - defensive guard
        message = f"Unsupported URL scheme '{url.scheme}' for GitHub API request."
        raise GithubReleaseError(message)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "release-to-pypi-action",
    }

    with httpx.Client(timeout=httpx.Timeout(30.0), headers=headers) as client:
        response = client.get(url, follow_redirects=False)

    if response.status_code == httpx.codes.OK:
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - unexpected payload
            truncated_payload = _truncate_text(
                response.text,
                _JSON_PAYLOAD_PREVIEW_LIMIT,
                suffix="...",
            )
            message = (
                "GitHub API returned invalid JSON. "
                f"Raw payload (truncated): {truncated_payload}"
            )
            raise GithubReleaseError(message) from exc

    _handle_http_response_error(response, tag)


@retry(
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=_retry_wait_strategy(),
    retry=retry_if_exception_type((httpx.RequestError, GithubReleaseRetryError)),
    reraise=True,
)
def _fetch_release_with_retry(repo: str, tag: str, token: str) -> dict[str, object]:
    return _request_release(repo, tag, token)


def _fetch_release(repo: str, tag: str, token: str) -> dict[str, object]:
    try:
        return _fetch_release_with_retry(repo, tag, token)
    except GithubReleaseRetryError as exc:
        message = str(exc) or "GitHub API request failed after retries."
        raise GithubReleaseError(message) from exc
    except httpx.RequestError as exc:  # pragma: no cover - network failure path
        message = f"Failed to reach GitHub API: {exc!s}"
        raise GithubReleaseError(message) from exc


def _validate_release(tag: str, data: dict[str, object]) -> str:
    draft = data.get("draft")
    prerelease = data.get("prerelease")
    name = data.get("name") or tag

    if draft:
        message = (
            f"Release '{name}' for {tag} is still a draft. "
            "Publish it before running this action."
        )
        raise GithubReleaseError(message)
    if prerelease:
        message = (
            f"Release '{name}' for {tag} is marked as prerelease. "
            "Publish a normal release first."
        )
        raise GithubReleaseError(message)

    return str(name)


@app.default
def main(
    *,
    tag: typ.Annotated[str, Parameter(env_var="RELEASE_TAG", required=True)],
    token: typ.Annotated[str, Parameter(env_var="GH_TOKEN", required=True)],
    repo: typ.Annotated[str, Parameter(env_var="GITHUB_REPOSITORY", required=True)],
) -> None:
    """Check that the GitHub release for ``tag`` is published."""
    try:
        data = _fetch_release(repo, tag, token)
        name = _validate_release(tag, data)
    except GithubReleaseError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"GitHub Release '{name}' is published.")


if __name__ == "__main__":
    app()
