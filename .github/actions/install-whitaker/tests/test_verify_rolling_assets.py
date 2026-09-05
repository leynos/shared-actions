"""Contracts for the pre-install check on Whitaker's rolling assets.

The installer falls back to `cargo install` when a rolling asset is missing,
and that fallback succeeds. A run that took it looks like a working run while
having built its lint tooling from unpinned sources, slowly. The check exists
to make that outcome impossible to reach silently, so its own failure has to be
loud, bounded and specific.
"""

from __future__ import annotations

import importlib.util
import json
import typing as typ
import urllib.error
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "verify_rolling_assets.py"
)
_spec = importlib.util.spec_from_file_location("verify_rolling_assets", _MODULE_PATH)
assert _spec is not None
assert _spec.loader is not None
verify_rolling_assets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_rolling_assets)

TARGET = "x86_64-unknown-linux-gnu"
TOOLCHAIN = "nightly-2026-05-28"
SHA = "3bf44b2"
ARCHIVE = f"whitaker-lints-{SHA}-{TOOLCHAIN}-{TARGET}.tar.zst"
COMPLETE = (
    f"manifest-{TARGET}.json",
    f"cargo-dylint-{TARGET}-v6.0.1.tgz",
    f"dylint-link-{TARGET}-v6.0.1.tgz",
    ARCHIVE,
)
MANIFEST = {"git_sha": SHA, "toolchain": TOOLCHAIN, "target": TARGET}


def _responses(
    monkeypatch: pytest.MonkeyPatch, pages: cabc.Sequence[tuple[str, ...] | Exception]
) -> list[int]:
    """Serve one release listing per attempt, and a fixed manifest.

    Returns a single-element list holding the number of listings served, so a
    test can assert the retry stopped rather than ran to its bound.
    """
    served = [0]

    def fake_read(url: str, token: str | None) -> bytes:
        if url.endswith(".json"):
            return json.dumps(MANIFEST).encode()
        index = min(served[0], len(pages) - 1)
        served[0] += 1
        page = pages[index]
        if isinstance(page, Exception):
            raise page
        return json.dumps({"assets": [{"name": name} for name in page]}).encode()

    monkeypatch.setattr(verify_rolling_assets, "_read", fake_read)
    return served


def _attempt(count: int = 3) -> verify_rolling_assets.Attempt:
    """Return a retry schedule that does not sleep."""
    return verify_rolling_assets.Attempt(count, 0.0)


def test_a_complete_release_returns_the_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nightly is what a caller records, so it must come back."""
    served = _responses(monkeypatch, [COMPLETE])

    assert verify_rolling_assets.verify(TARGET, _attempt(), None) == TOOLCHAIN
    assert served[0] == 1, "a complete release must not be polled again"


@pytest.mark.parametrize(
    ("omitted", "expected"),
    [
        pytest.param(
            f"cargo-dylint-{TARGET}-v6.0.1.tgz", "cargo-dylint-", id="cargo-dylint"
        ),
        pytest.param(
            f"dylint-link-{TARGET}-v6.0.1.tgz", "dylint-link-", id="dylint-link"
        ),
        pytest.param(ARCHIVE, "whitaker-lints-", id="lint-archive"),
    ],
)
def test_a_missing_asset_fails_closed_and_names_it(
    monkeypatch: pytest.MonkeyPatch, omitted: str, expected: str
) -> None:
    """Failing without naming the asset leaves an operator nowhere to start."""
    incomplete = tuple(name for name in COMPLETE if name != omitted)
    _responses(monkeypatch, [incomplete])

    with pytest.raises(verify_rolling_assets.AssetsUnavailableError) as error:
        verify_rolling_assets.verify(TARGET, _attempt(), None)

    assert expected in str(error.value)
    assert "releases/download/rolling" in str(error.value), (
        "the message must carry the URL, since the next step is to look there"
    )


def test_a_short_absence_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A republish takes seconds, so arriving mid-publish must not fail.

    This is the whole reason the check retries: the window it guards against
    is the same window a caller may legitimately land in.
    """
    incomplete = tuple(name for name in COMPLETE if name != ARCHIVE)
    served = _responses(monkeypatch, [incomplete, incomplete, COMPLETE])

    assert verify_rolling_assets.verify(TARGET, _attempt(), None) == TOOLCHAIN
    assert served[0] == 3


def test_a_transport_failure_is_retried_then_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit or a blip must not be mistaken for a missing asset."""
    failure = urllib.error.URLError("connection reset")
    _responses(monkeypatch, [failure])

    with pytest.raises(verify_rolling_assets.AssetsUnavailableError) as error:
        verify_rolling_assets.verify(TARGET, _attempt(), None)

    assert "could not read the rolling release" in str(error.value)


def test_a_manifest_without_a_toolchain_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest missing its fields cannot confirm anything.

    Deriving the archive name from an absent `git_sha` would produce a name no
    release could hold, so the failure would read as a missing archive rather
    than a malformed manifest.
    """
    monkeypatch.setattr(
        verify_rolling_assets,
        "_read",
        lambda url, token: (
            json.dumps({"target": TARGET}).encode()
            if url.endswith(".json")
            else json.dumps({"assets": [{"name": n} for n in COMPLETE]}).encode()
        ),
    )

    with pytest.raises(verify_rolling_assets.AssetsUnavailableError) as error:
        verify_rolling_assets.verify(TARGET, _attempt(1), None)

    assert "git_sha or toolchain" in str(error.value)


def test_the_command_line_reports_the_toolchain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The caller reads the nightly from stdout, so it must be alone there."""
    _responses(monkeypatch, [COMPLETE])

    exit_code = verify_rolling_assets.main(
        ["--target", TARGET, "--attempts", "1", "--interval", "0"]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == TOOLCHAIN


def test_the_command_line_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing asset must exit non-zero with an annotation."""
    _responses(monkeypatch, [tuple(n for n in COMPLETE if n != ARCHIVE)])

    exit_code = verify_rolling_assets.main(
        ["--target", TARGET, "--attempts", "1", "--interval", "0"]
    )

    assert exit_code == 1
    assert "::error title=Whitaker rolling assets::" in capsys.readouterr().err
