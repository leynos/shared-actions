#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "cyclopts>=2.9,<3.0",
#     "httpx>=0.28,<0.29",
#     "httpx-retries>=0.4,<0.5",
# ]
# ///
"""Verify that the GitHub Release for the provided tag exists and is published."""

from __future__ import annotations

import contextlib
import random
import sys
import time
import typing as typ
from dataclasses import dataclass  # noqa: ICN003

import cyclopts
import httpx
from cyclopts import App, Parameter
from httpx_retries import Retry, RetryTransport

app = App(config=cyclopts.config.Env(prefix="", command=False))


class _UniformGenerator(typ.Protocol):
    """Protocol describing RNG objects that provide ``uniform``."""

    def uniform(self, a: float, b: float) -> float:
        """Return a random floating point number N such that ``a <= N <= b``."""


SleepFn = typ.Callable[[float], None]

_JITTER = random.SystemRandom()


def _sleep_with_jitter(
    delay: float,
    *,
    jitter: _UniformGenerator | None = None,
    sleep: SleepFn | None = None,
) -> None:
    """Sleep for ``delay`` seconds with a deterministic jitter hook for tests."""
    sleep_base = max(delay, 0.0)
    jitter_source = _JITTER if jitter is None else jitter
    sleep_fn = time.sleep if sleep is None else sleep
    jitter_amount = sleep_base * jitter_source.uniform(0.0, 0.1)
    sleep_fn(sleep_base + jitter_amount)


class GithubReleaseError(RuntimeError):
    """Raised when the GitHub release is not ready for publishing."""


_MAX_ATTEMPTS = 5
_BACKOFF_FACTOR = 1.5
_INITIAL_DELAY = 1.0
_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504, 429})
_ERROR_DETAIL_LIMIT = 1024
_JSON_PAYLOAD_PREVIEW_LIMIT = 500


@dataclass(frozen=True, kw_only=True)
class RetryConfig:
    """Configuration for retry behaviour."""

    total: int | None = None
    allowed_methods: typ.Iterable[str] | None = None
    status_forcelist: typ.Iterable[int] | None = None
    retry_on_exceptions: typ.Iterable[type[Exception]] | None = None
    backoff_factor: float = 0.0
    respect_retry_after_header: bool = True
    max_backoff_wait: float = 120.0
    backoff_jitter: float = 0.0


_T = typ.TypeVar("_T")
_U = typ.TypeVar("_U")


def _resolve_optional(
    value: _T | None,
    default: _T,
    *,
    transform: typ.Callable[[_T], _U] | None = None,
) -> _T | _U:
    if value is None:
        return default
    if transform is None:
        return value
    return transform(value)


def _normalize_allowed_methods(
    allowed_methods: typ.Iterable[str] | None,
) -> frozenset[str]:
    return _resolve_optional(
        allowed_methods,
        frozenset({"GET"}),
        transform=lambda methods: frozenset(str(method).upper() for method in methods),
    )


def _validate_status_forcelist(
    status_forcelist: typ.Iterable[int] | None,
) -> frozenset[int]:
    def _validator(values: typ.Iterable[int]) -> frozenset[int]:
        validated: set[int] = set()
        for code in values:
            try:
                validated.add(int(code))
            except (TypeError, ValueError) as exc:
                message = (
                    "Invalid status code in status_forcelist: "
                    f"{code!r} is not an integer"
                )
                raise ValueError(message) from exc
        return frozenset(validated)

    return _resolve_optional(
        status_forcelist,
        _RETRYABLE_STATUS_CODES,
        transform=_validator,
    )


def _normalize_retry_exceptions(
    retry_on_exceptions: typ.Iterable[type[Exception]] | None,
) -> tuple[type[Exception], ...]:
    return _resolve_optional(
        retry_on_exceptions,
        Retry.RETRYABLE_EXCEPTIONS,
        transform=lambda values: tuple(values),
    )


class _GithubRetry(Retry):
    """Retry configuration that mirrors the action's backoff strategy."""

    def __init__(
        self,
        config: RetryConfig | None = None,
        *,
        attempts_made: int = 0,
    ) -> None:
        cfg = RetryConfig() if config is None else config
        self._config: RetryConfig = cfg
        super().__init__(
            total=_resolve_optional(cfg.total, _MAX_ATTEMPTS - 1),
            allowed_methods=_normalize_allowed_methods(cfg.allowed_methods),
            status_forcelist=_validate_status_forcelist(cfg.status_forcelist),
            retry_on_exceptions=_normalize_retry_exceptions(cfg.retry_on_exceptions),
            backoff_factor=cfg.backoff_factor,
            respect_retry_after_header=cfg.respect_retry_after_header,
            max_backoff_wait=cfg.max_backoff_wait,
            backoff_jitter=cfg.backoff_jitter,
            attempts_made=attempts_made,
        )

    def increment(self) -> _GithubRetry:
        return self.__class__(
            config=self._config,
            attempts_made=self.attempts_made + 1,
        )

    def backoff_strategy(self) -> float:  # pragma: no cover - exercised via sleep()
        exponent = max(self.attempts_made - 1, 0)
        delay = _INITIAL_DELAY * (_BACKOFF_FACTOR**exponent)
        return min(delay, self.max_backoff_wait)

    def sleep(self, response: httpx.Response | httpx.HTTPError) -> None:
        headers: httpx.Headers
        if isinstance(response, httpx.Response):
            headers = response.headers
        else:  # pragma: no cover - defensive branch for transport errors
            headers = httpx.Headers()

        if self.respect_retry_after_header:
            retry_after = headers.get("Retry-After", "").strip()
            if retry_after:
                with contextlib.suppress(ValueError):
                    seconds = min(
                        self.parse_retry_after(retry_after), self.max_backoff_wait
                    )
                    if seconds > 0:
                        time.sleep(seconds)
                        return

        delay = self.backoff_strategy()
        if delay > 0:
            _sleep_with_jitter(delay)


def _build_retry_transport() -> RetryTransport:
    """Construct a retry transport for GitHub API requests."""
    return RetryTransport(retry=_GithubRetry())


def _parse_retry_after_header(value: str | None) -> float | None:
    """Return a parsed ``Retry-After`` delay in seconds when available."""
    if value is None:
        return None
    helper = _GithubRetry()
    with contextlib.suppress(ValueError):
        seconds = helper.parse_retry_after(value)
        if seconds > 0:
            return seconds
    return None


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


def _fetch_release(repo: str, tag: str, token: str) -> dict[str, object]:
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

    try:
        with httpx.Client(
            timeout=httpx.Timeout(30.0),
            transport=_build_retry_transport(),
            headers=headers,
        ) as client:
            delay = _INITIAL_DELAY
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    response = client.get(url, follow_redirects=False)
                except httpx.RequestError as exc:  # pragma: no cover
                    # Network failure path.
                    if attempt == _MAX_ATTEMPTS:
                        message = f"Failed to reach GitHub API: {exc!s}"
                        raise GithubReleaseError(message) from exc
                    _sleep_with_jitter(delay)
                    delay *= _BACKOFF_FACTOR
                    continue

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

                should_retry = _handle_http_response_error(
                    response,
                    tag,
                    attempt=attempt,
                )
                if should_retry:
                    delay *= _BACKOFF_FACTOR
                    continue

            message = "GitHub API request failed after retries."
            raise GithubReleaseError(message)
    except httpx.RequestError as exc:  # pragma: no cover - network failure path
        message = f"Failed to reach GitHub API: {exc!s}"
        raise GithubReleaseError(message) from exc


def _handle_http_response_error(
    response: httpx.Response,
    tag: str,
    *,
    attempt: int,
) -> bool:
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
        if retry_after is not None and attempt < _MAX_ATTEMPTS:
            time.sleep(retry_after)
            return True
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

    failure_reason = detail or "Unknown error"
    message = f"GitHub API request failed with status {status}: {failure_reason}"
    raise GithubReleaseError(message)


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
