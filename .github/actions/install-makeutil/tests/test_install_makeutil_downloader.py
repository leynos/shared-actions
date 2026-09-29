"""`default_downloader`: HTTPS-only transport, bounds and redirect handling.

The transport is exercised through the injectable opener, so no test opens a
socket. Redirects are proved with a stub HTTPS handler that serves canned
responses, which is what lets an HTTPS-to-HTTP hop be tested at all without a
live TLS server.
"""

from __future__ import annotations

import email.message
import http.client
import io
import typing as typ
import urllib.error
import urllib.request
import urllib.response

import makeutil_verify
import pytest
from makeutil_errors import DownloadError

_ASSET_URL = "https://github.com/leynos/makeutil/releases/download/v0.1.0/x"


class _StubHttpsHandler(urllib.request.BaseHandler):
    """Serve canned HTTPS responses keyed by URL, recording every request."""

    def __init__(self, responses: dict[str, tuple[int, str, bytes]]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        """Return the canned `(status, location, body)` for the request."""
        self.requested.append(req.full_url)
        status, location, body = self._responses[req.full_url]
        headers = email.message.Message()
        if location:
            headers["Location"] = location
        response = urllib.response.addinfourl(
            io.BytesIO(body), headers, req.full_url, status
        )
        response.msg = "stub"  # ty: ignore[unresolved-attribute]
        return response


class TestHttpsOnly:
    """The default downloader refuses anything but HTTPS."""

    def test_a_non_https_url_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `http://` URL is refused by name and never reaches `urlopen`.

        The opener is replaced with one that fails the test, so a refusal that
        came from the network rather than from the scheme check cannot pass.
        """

        def no_request() -> typ.NoReturn:
            msg = "no request must be made for a non-HTTPS URL"
            raise AssertionError(msg)

        monkeypatch.setattr(makeutil_verify, "_https_only_opener", no_request)

        with pytest.raises(DownloadError, match="non-HTTPS"):
            makeutil_verify.default_downloader(
                "http://github.com/leynos/makeutil/releases/download/x"
            )


class TestHttpsOnlyRedirects:
    """A redirect hop is held to the same HTTPS-only rule as the first URL."""

    @pytest.mark.parametrize("scheme", ["http", "ftp", "file"])
    def test_the_opener_refuses_a_non_https_hop(self, scheme: str) -> None:
        """The opener has no handler for a scheme other than HTTPS.

        A redirect to such a URL is opened through this same opener, so it
        fails as an unknown URL type before any request is made.
        """
        opener = makeutil_verify._https_only_opener()

        with pytest.raises(urllib.error.URLError, match="unknown url type"):
            opener.open(f"{scheme}://127.0.0.1:9/asset", timeout=5)

    def test_the_opener_still_follows_redirects(self) -> None:
        """Redirects stay enabled, because release assets redirect to a CDN."""
        opener = makeutil_verify._https_only_opener()

        assert any(
            isinstance(handler, urllib.request.HTTPRedirectHandler)
            for handler in opener.handlers  # ty: ignore[unresolved-attribute]
        )

    def test_the_default_downloader_uses_the_validating_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The downloader must open through the redirect-validating opener.

        Without this, the handler could be correct and simply never installed.
        """
        seen: list[str] = []

        class _Opener:
            def open(self, request: urllib.request.Request, **_kwargs: object) -> None:
                seen.append(request.full_url)
                message = "stop here"
                raise urllib.error.URLError(message)

        monkeypatch.setattr(makeutil_verify, "_https_only_opener", _Opener)

        with pytest.raises(DownloadError):
            makeutil_verify.default_downloader("https://github.com/leynos/x")

        assert seen == ["https://github.com/leynos/x"]


class TestHttpClientExceptions:
    """An `http.client.HTTPException` during the response body read must
    become a `DownloadError`, not escape as a traceback.
    """

    def test_an_incomplete_read_becomes_a_download_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`http.client.IncompleteRead` is a `HTTPException` subclass that
        `response.read()` can raise mid-body; it must be caught alongside
        the URL and OS errors already handled.
        """

        class _RaisingResponse:
            def __enter__(self) -> typ.Self:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

            def read(self, _size: int) -> bytes:
                partial = b""
                raise http.client.IncompleteRead(partial)

        class _FakeOpener:
            def open(self, *_args: object, **_kwargs: object) -> _RaisingResponse:
                return _RaisingResponse()

        monkeypatch.setattr(makeutil_verify, "_https_only_opener", _FakeOpener)

        with pytest.raises(DownloadError, match="could not download"):
            makeutil_verify.default_downloader(
                "https://github.com/leynos/makeutil/releases/download/x"
            )


class TestBounds:
    """The download is bounded in size and time."""

    def test_a_response_over_the_size_cap_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One byte past `_MAX_ASSET_BYTES` becomes a `DownloadError`.

        The response reports only the bytes requested, so the cap is proved by
        the size the downloader asks `read` for, not by allocating 50 MiB.
        """
        limit = makeutil_verify._MAX_ASSET_BYTES
        requested: list[int] = []

        class _Response:
            def __enter__(self) -> typ.Self:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                requested.append(size)
                return b"x" * (limit + 1) if size > limit else b"x"

        class _Opener:
            def open(self, *_args: object, **_kwargs: object) -> _Response:
                return _Response()

        monkeypatch.setattr(makeutil_verify, "_https_only_opener", _Opener)

        with pytest.raises(DownloadError, match="exceeds"):
            makeutil_verify.default_downloader(_ASSET_URL)

        assert requested == [limit + 1]

    def test_the_request_carries_the_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The opener is always called with `DOWNLOAD_TIMEOUT_SECONDS`."""
        seen: dict[str, object] = {}

        class _Opener:
            def open(self, _request: object, **kwargs: object) -> None:
                seen.update(kwargs)
                message = "stop here"
                raise urllib.error.URLError(message)

        monkeypatch.setattr(makeutil_verify, "_https_only_opener", _Opener)

        with pytest.raises(DownloadError):
            makeutil_verify.default_downloader(_ASSET_URL)

        assert seen == {"timeout": makeutil_verify.DOWNLOAD_TIMEOUT_SECONDS}


class TestHttpsRedirectHops:
    """A live redirect chain, served by a stub HTTPS handler."""

    @pytest.mark.parametrize("target", ["http://cdn.example/a", "ftp://cdn.example/a"])
    def test_an_https_to_cleartext_redirect_is_refused(self, target: str) -> None:
        """The first hop is HTTPS; the redirect target is not, so the hop
        fails as an unknown URL type and the cleartext URL is never requested.
        """
        stub = _StubHttpsHandler({_ASSET_URL: (302, target, b"")})
        opener = makeutil_verify._https_only_opener(stub)

        with pytest.raises(urllib.error.URLError, match="unknown url type"):
            opener.open(_ASSET_URL, timeout=5)

        assert stub.requested == [_ASSET_URL]

    def test_an_https_to_https_redirect_is_followed(self) -> None:
        """A CDN hop over HTTPS delivers the body, as GitHub's assets do."""
        cdn = "https://objects.example/asset"
        stub = _StubHttpsHandler(
            {_ASSET_URL: (302, cdn, b""), cdn: (200, "", b"payload")}
        )
        opener = makeutil_verify._https_only_opener(stub)

        with opener.open(_ASSET_URL, timeout=5) as response:
            body = response.read()

        assert body == b"payload"
        assert stub.requested == [_ASSET_URL, cdn]
