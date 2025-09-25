#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.17,<0.18",
#     "httpx>=0.28,<0.29",
#     "httpx-retries>=0.4,<0.5",
# ]
# ///
"""Verify that the GitHub Release for the provided tag exists and is published."""

from __future__ import annotations

import contextlib
import random
import time
import typing as typ

import httpx
import typer
from httpx_retries import Retry, RetryTransport

TAG_OPTION = typer.Option(..., envvar="RELEASE_TAG")
TOKEN_OPTION = typer.Option(..., envvar="GH_TOKEN")
REPO_OPTION = typer.Option(..., envvar="GITHUB_REPOSITORY")


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


class _GithubRetry(Retry):
    """Retry configuration that mirrors the action's backoff strategy."""

    def __init__(self) -> None:
        super().__init__(
            total=_MAX_ATTEMPTS - 1,
            backoff_factor=0.0,
            backoff_jitter=0.0,
            status_forcelist=_RETRYABLE_STATUS_CODES,
            allowed_methods=frozenset({"GET"}),
            retry_on_exceptions=self.RETRYABLE_EXCEPTIONS,
            respect_retry_after_header=True,
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
            response = client.get(url, follow_redirects=False)
    except httpx.RequestError as exc:  # pragma: no cover - network failure path
        message = f"Failed to reach GitHub API: {exc!s}"
        raise GithubReleaseError(message) from exc

    if response.status_code != httpx.codes.OK:
        _handle_http_response_error(response, tag)

    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - unexpected payload
        message = "GitHub API returned invalid JSON"
        raise GithubReleaseError(message) from exc


def _handle_http_response_error(response: httpx.Response, tag: str) -> None:
    status = response.status_code
    detail = response.text.strip() or response.reason_phrase
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


def main(
    tag: str = TAG_OPTION,
    token: str = TOKEN_OPTION,
    repo: str = REPO_OPTION,
) -> None:
    """Check that the GitHub release for ``tag`` is published.

    Parameters
    ----------
    tag : str
        Release tag to validate.
    token : str
        Token used to authenticate the GitHub API request.
    repo : str
        Repository slug in ``owner/name`` form where the release should exist.

    Raises
    ------
    typer.Exit
        Raised when the release is missing or not ready for publication.
    """
    try:
        data = _fetch_release(repo, tag, token)
        name = _validate_release(tag, data)
    except GithubReleaseError as exc:
        typer.echo(f"::error::{exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"GitHub Release '{name}' is published.")


if __name__ == "__main__":
    typer.run(main)
