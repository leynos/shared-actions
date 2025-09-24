#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.17,<0.18"]
# ///
"""Verify that the GitHub Release for the provided tag exists and is published."""

from __future__ import annotations

import contextlib
import json
import random
import time
import typing as typ
import urllib.error
import urllib.parse
import urllib.request

import typer

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


def _fetch_release(repo: str, tag: str, token: str) -> dict[str, object]:
    request = _build_release_request(repo, tag, token)
    payload = _request_with_retry(request, tag)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - unexpected payload
        message = "GitHub API returned invalid JSON"
        raise GithubReleaseError(message) from exc


def _build_release_request(repo: str, tag: str, token: str) -> urllib.request.Request:
    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    parsed = urllib.parse.urlsplit(api)
    if parsed.scheme != "https":  # pragma: no cover - defensive guard
        message = f"Unsupported URL scheme '{parsed.scheme}' for GitHub API request."
        raise GithubReleaseError(message)
    return urllib.request.Request(  # noqa: S310 - https scheme enforced above
        api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "release-to-pypi-action",
        },
    )


def _request_with_retry(request: urllib.request.Request, tag: str) -> str:
    delay = _INITIAL_DELAY
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network failure path
            delay = _handle_http_error(exc, attempt, delay, tag)
        except urllib.error.URLError as exc:  # pragma: no cover - network failure path
            if attempt == _MAX_ATTEMPTS:
                message = f"Failed to reach GitHub API: {exc.reason}"
                raise GithubReleaseError(message) from exc
            _sleep_with_jitter(delay)
            delay *= _BACKOFF_FACTOR
    message = "GitHub API request failed after retries."
    raise GithubReleaseError(message)


def _handle_http_error(
    exc: urllib.error.HTTPError, attempt: int, delay: float, tag: str
) -> float:
    detail = _extract_error_detail(exc)
    match exc.code:
        case 401:
            context = detail or exc.reason
            message = (
                "GitHub rejected the token (401 Unauthorized). "
                "Verify that GH_TOKEN is correct and has not expired."
            )
            if context:
                message = f"{message} ({context})"
            raise GithubReleaseError(message) from exc
        case 403:
            permission_message = (
                "GitHub token lacks permission to read releases "
                "or has expired. "
                "Use a token with contents:read scope."
            )
            context = detail or exc.reason
            message = f"{permission_message} ({context})"
            raise GithubReleaseError(message) from exc
        case 404:
            message = (
                "No GitHub release found for tag "
                f"{tag}. Create and publish the release first."
            )
            raise GithubReleaseError(message) from exc
    if attempt == _MAX_ATTEMPTS:
        failure_reason = detail or exc.reason
        message = f"GitHub API request failed with status {exc.code}: {failure_reason}"
        raise GithubReleaseError(message) from exc

    sleep_delay = _retry_delay(exc, delay)
    _sleep_with_jitter(sleep_delay)
    return sleep_delay * _BACKOFF_FACTOR


def _retry_delay(exc: urllib.error.HTTPError, delay: float) -> float:
    retry_after = None
    if hasattr(exc, "headers") and exc.headers is not None:
        retry_after = exc.headers.get("Retry-After")
    if retry_after:
        with contextlib.suppress(Exception):
            return float(retry_after)
    return delay


def _extract_error_detail(exc: urllib.error.HTTPError) -> str:
    if hasattr(exc, "read"):
        return exc.read().decode("utf-8", errors="ignore")
    return ""


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
