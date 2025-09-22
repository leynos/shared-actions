#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.17,<0.18"]
# ///
"""Verify that the GitHub Release for the provided tag exists and is published."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import typer

TAG_OPTION = typer.Option(..., envvar="RELEASE_TAG")
TOKEN_OPTION = typer.Option(..., envvar="GH_TOKEN")
REPO_OPTION = typer.Option(..., envvar="GITHUB_REPOSITORY")


class GithubReleaseError(RuntimeError):
    """Raised when the GitHub release is not ready for publishing."""


def _fetch_release(repo: str, tag: str, token: str) -> dict[str, object]:
    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    parsed = urllib.parse.urlsplit(api)
    if parsed.scheme != "https":  # pragma: no cover - defensive guard
        message = f"Unsupported URL scheme '{parsed.scheme}' for GitHub API request."
        raise GithubReleaseError(message)
    request = urllib.request.Request(  # noqa: S310 - https scheme enforced above
        api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "release-to-pypi-action",
        },
    )
    max_attempts = 5
    backoff_factor = 1.5
    delay = 1.0
    payload: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:  # pragma: no cover - network failure path
            detail = (
                exc.read().decode("utf-8", errors="ignore")
                if hasattr(exc, "read")
                else ""
            )
            if exc.code == 404:
                message = (
                    "No GitHub release found for tag "
                    f"{tag}. Create and publish the release first."
                )
                raise GithubReleaseError(message) from exc
            if exc.code == 403:
                permission_message = (
                    "GitHub token lacks permission to read releases or has expired. "
                    "Use a token with contents:read scope."
                )
                context = detail or exc.reason
                message = f"{permission_message} ({context})"
                raise GithubReleaseError(message) from exc
            if attempt == max_attempts:
                failure_reason = detail or exc.reason
                message = (
                    "GitHub API request failed with status "
                    f"{exc.code}: {failure_reason}"
                )
                raise GithubReleaseError(message) from exc
            time.sleep(delay)
            delay *= backoff_factor
        except urllib.error.URLError as exc:  # pragma: no cover - network failure path
            if attempt == max_attempts:
                message = f"Failed to reach GitHub API: {exc.reason}"
                raise GithubReleaseError(message) from exc
            time.sleep(delay)
            delay *= backoff_factor
    else:  # pragma: no cover - loop exhausted without break
        message = "GitHub API request failed after retries."
        raise GithubReleaseError(message)

    try:
        return json.loads(payload or "")
    except json.JSONDecodeError as exc:  # pragma: no cover - unexpected payload
        message = "GitHub API returned invalid JSON"
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
