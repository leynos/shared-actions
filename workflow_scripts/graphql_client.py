"""GitHub GraphQL API client with retry logic.

This module provides a simple GraphQL client for the GitHub API with
exponential backoff retry handling for transient failures.
"""

from __future__ import annotations

import json
import time
import typing as typ

import httpx

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

# Type alias for JSON-compatible values (parsed from json.loads)
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


def _fail(message: str) -> typ.NoReturn:
    """Log an error and exit with status code 1."""
    import sys

    print("automerge_status=error", file=sys.stderr)
    print(f"automerge_error={message}", file=sys.stderr)
    raise SystemExit(1)


def _should_retry(attempt: int, max_retries: int) -> bool:
    """Return True if more retry attempts remain."""
    return attempt < max_retries


def _backoff_sleep(attempt: int, base_seconds: float = 1.0) -> None:
    """Sleep with exponential backoff based on attempt number."""
    time.sleep(base_seconds * (2**attempt))


def _attempt_retry_or_fail(attempt: int, max_retries: int, error_message: str) -> None:
    """Sleep for backoff if retries remain, otherwise fail with the given message."""
    if not _should_retry(attempt, max_retries):
        _fail(error_message)
    _backoff_sleep(attempt)


def _parse_graphql_response(response: httpx.Response) -> dict[str, JsonValue]:
    """Parse and validate a GraphQL response, returning the data payload."""
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        _fail(f"GitHub API response was not valid JSON: {exc}")

    errors = payload.get("errors")
    if errors:
        _fail(f"GitHub API GraphQL errors: {errors}")

    data = payload.get("data")
    if data is None:
        _fail("GitHub API returned no data.")
    return data


def _execute_graphql_attempt(
    token: str, query: str, variables: dict[str, JsonValue]
) -> httpx.Response | None:
    """Execute a single GraphQL request attempt, returning None on connection error."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        with httpx.Client(timeout=30) as client:
            return client.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                headers=headers,
            )
    except httpx.TransportError:
        return None


def _handle_response_or_retry(
    response: httpx.Response | None, attempt: int, max_retries: int
) -> httpx.Response | None:
    """Process response or decide to retry. Returns None to signal retry."""
    if response is None:
        _attempt_retry_or_fail(
            attempt,
            max_retries,
            "GitHub API request failed after retries: connection error",
        )
        return None

    if 400 <= response.status_code < 500:
        _fail(f"GitHub API error {response.status_code}: {response.text}")

    if response.status_code >= 500:
        msg = f"GitHub API error {response.status_code} after retries"
        _attempt_retry_or_fail(attempt, max_retries, f"{msg}: {response.text}")
        return None

    return response


def request_graphql(
    token: str, query: str, variables: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Execute a GraphQL request with retry logic and return the data payload."""
    max_retries = 3

    for attempt in range(max_retries + 1):
        response = _execute_graphql_attempt(token, query, variables)
        processed_response = _handle_response_or_retry(response, attempt, max_retries)
        if processed_response is None:
            continue
        return _parse_graphql_response(processed_response)

    _fail("GitHub API request failed unexpectedly.")
