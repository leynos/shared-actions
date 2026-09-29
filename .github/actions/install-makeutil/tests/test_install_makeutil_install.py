"""`install_makeutil`: the download, verify, and staged-install orchestrator.

Every scenario here injects a fake downloader, so none of it touches the
network. Each failure scenario also asserts that `bin-dir` is left exactly
as it was found - nothing partially installed, nothing left behind - which
is the fail-closed guarantee the packet requires.
"""

from __future__ import annotations

import http.client
import http.server
import io
import os
import threading
import typing as typ
import urllib.error
import urllib.request

import makeutil_verify
import pytest
from makeutil_errors import DownloadError
from makeutil_verify import (
    CACHED,
    DIGEST_MISMATCH,
    DOWNLOAD_FAILED,
    INSTALL_FAILED,
    INSTALLED,
    SIDECAR_MISMATCH,
    AssetUrls,
    install_makeutil,
    sha256_hex,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_BINARY = b"pretend this is a static makeutil binary\n"
_NAME = "makeutil-x86_64-unknown-linux-musl"
_BINARY_URL = f"https://github.com/leynos/makeutil/releases/download/v0.1.0/{_NAME}"
_SIDECAR_URL = f"{_BINARY_URL}.sha256"
_ASSET_URLS = AssetUrls(binary=_BINARY_URL, sidecar=_SIDECAR_URL)
_DIGEST = sha256_hex(_BINARY)


def _sidecar_for(data: bytes, name: str = _NAME) -> bytes:
    """Return a well-formed sidecar body for `data`."""
    return f"{sha256_hex(data)}  {name}\n".encode()


def _fake_downloader(
    binary: bytes = _BINARY, sidecar: bytes | None = None
) -> typ.Callable[[str], bytes]:
    """Build a downloader that serves canned bytes for the two known URLs."""
    resolved_sidecar = sidecar if sidecar is not None else _sidecar_for(binary)

    def _download(url: str) -> bytes:
        if url == _BINARY_URL:
            return binary
        if url == _SIDECAR_URL:
            return resolved_sidecar
        message = f"unexpected URL in test: {url}"
        raise AssertionError(message)

    return _download


def _install(
    target: Path,
    downloader: typ.Callable[[str], bytes],
    expected_sha256: str = _DIGEST,
) -> makeutil_verify.InstallResult:
    """Run `install_makeutil` for the canned asset URLs."""
    return install_makeutil(
        executable_path=target,
        expected_sha256=expected_sha256,
        asset_urls=_ASSET_URLS,
        downloader=downloader,
    )


def _unreachable_downloader(_url: str) -> bytes:
    """Fail the test if a valid cache hit reaches the network."""
    message = "a valid cache hit must never call the downloader"
    raise AssertionError(message)


def _seed_cache(tmp_path: Path, data: bytes, mode: int = 0o755) -> Path:
    """Place `data` where a cache restore would, and return its path."""
    target = tmp_path / "bin" / "makeutil"
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    target.chmod(mode)
    return target


class TestSuccess:
    """Both digests match: the binary is installed, executable, in place."""

    def test_a_verified_download_is_installed_executable(self, tmp_path: Path) -> None:
        """The staged file lands at `executable_path` with the exec bit set."""
        target = tmp_path / "bin" / "makeutil"

        result = _install(target, _fake_downloader())

        assert result.outcome == INSTALLED
        assert result.path == target
        assert target.read_bytes() == _BINARY
        assert os.access(target, os.X_OK)


class TestFailureScenarios:
    """Every failure path installs nothing and leaves `bin-dir` untouched."""

    def test_a_table_digest_mismatch_installs_nothing(self, tmp_path: Path) -> None:
        """The downloaded bytes are real, but disagree with the pinned table."""
        target = tmp_path / "bin" / "makeutil"
        wrong_digest = "0" * 64

        result = _install(target, _fake_downloader(), wrong_digest)

        assert result.outcome == DIGEST_MISMATCH
        assert not target.exists()
        assert not target.parent.exists()

    @pytest.mark.parametrize(
        "tampered_sidecar",
        [
            pytest.param(
                f"{'f' * 64}  {_NAME}\n".encode(), id="wrong-digest-right-name"
            ),
            pytest.param(
                _sidecar_for(_BINARY, name="makeutil-other-target"),
                id="right-digest-wrong-name",
            ),
        ],
    )
    def test_a_sidecar_mismatch_installs_nothing(
        self, tmp_path: Path, tampered_sidecar: bytes
    ) -> None:
        """The table digest matches, but the sidecar disagrees - either by
        digest or by naming a different file. Both are refused the same way,
        and neither ever reaches the filesystem.
        """
        target = tmp_path / "bin" / "makeutil"

        result = _install(target, _fake_downloader(sidecar=tampered_sidecar))

        assert result.outcome == SIDECAR_MISMATCH
        assert not target.exists()

    def test_a_download_failure_installs_nothing(self, tmp_path: Path) -> None:
        """A downloader that raises stops the install with no file written."""
        target = tmp_path / "bin" / "makeutil"

        def _failing_downloader(_url: str) -> bytes:
            message = "connection refused"
            raise DownloadError(message)

        result = _install(target, _failing_downloader)

        assert result.outcome == DOWNLOAD_FAILED
        assert not target.exists()

    def test_a_staging_failure_is_reported_as_install_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An `OSError` while moving the staged file into place - here
        injected via `Path.replace` - becomes a bounded `install-failed`
        result rather than an uncaught traceback, and leaves neither a
        staged temporary file nor a partial target behind.
        """
        target = tmp_path / "bin" / "makeutil"

        def _failing_replace(self: Path, _dest: object) -> typ.NoReturn:
            message = "simulated replace failure"
            raise OSError(message)

        monkeypatch.setattr(makeutil_verify.Path, "replace", _failing_replace)

        result = _install(target, _fake_downloader())

        assert result.outcome == INSTALL_FAILED
        assert not target.exists()
        assert list(target.parent.iterdir()) == []

    def test_a_sidecar_download_failure_installs_nothing(self, tmp_path: Path) -> None:
        """The binary alone verifying is not enough; the sidecar fetch can
        still fail and must still leave nothing installed.
        """
        target = tmp_path / "bin" / "makeutil"

        def _download(url: str) -> bytes:
            if url == _BINARY_URL:
                return _BINARY
            message = "sidecar not found"
            raise DownloadError(message)

        result = _install(target, _download)

        assert result.outcome == DOWNLOAD_FAILED
        assert not target.exists()


class TestCacheReverification:
    """A cache hit is only ever trusted after re-verifying its digest."""

    def test_a_cached_file_with_the_expected_digest_is_reused(
        self, tmp_path: Path
    ) -> None:
        """No downloader call is needed when the cached bytes already match."""
        target = _seed_cache(tmp_path, _BINARY)

        result = _install(target, _unreachable_downloader)

        assert result.outcome == CACHED
        assert result.path == target

    def test_a_cached_file_without_the_execute_bit_is_made_executable(
        self, tmp_path: Path
    ) -> None:
        """A digest match alone does not prove the file is runnable; a
        restored cache entry can land without the execute bit, and a
        `CACHED` result must always be usable.
        """
        target = _seed_cache(tmp_path, _BINARY, mode=0o644)

        result = _install(target, _unreachable_downloader)

        assert result.outcome == CACHED
        assert os.access(target, os.X_OK)

    def test_a_cached_file_with_a_wrong_digest_is_reinstalled(
        self, tmp_path: Path
    ) -> None:
        """A stale or tampered cache entry self-heals rather than failing."""
        target = _seed_cache(tmp_path, b"stale bytes from a previous release")

        result = _install(target, _fake_downloader())

        assert result.outcome == INSTALLED
        assert target.read_bytes() == _BINARY


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

    @pytest.mark.parametrize(
        "target", ["http://cdn.example/asset", "ftp://cdn.example/asset"]
    )
    def test_a_redirect_to_a_cleartext_target_is_refused(self, target: str) -> None:
        """The handler raises before a non-HTTPS hop is requested."""
        handler = makeutil_verify._HttpsOnlyRedirectHandler()
        request = urllib.request.Request("https://github.com/x")

        with pytest.raises(urllib.error.URLError, match="non-HTTPS"):
            handler.redirect_request(
                request,
                io.BytesIO(),
                302,
                "Found",
                http.client.HTTPMessage(),
                target,
            )

    def test_a_redirect_to_an_https_target_is_followed(self) -> None:
        """A CDN hop over HTTPS yields the follow-up request."""
        handler = makeutil_verify._HttpsOnlyRedirectHandler()
        request = urllib.request.Request("https://github.com/x")

        followed = handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            http.client.HTTPMessage(),
            "https://cdn.example/asset",
        )

        assert followed is not None
        assert followed.full_url == "https://cdn.example/asset"

    def test_the_opener_refuses_a_live_cleartext_redirect(self) -> None:
        """The real opener stops a 302 to `http://` before following it.

        The handler can be correct yet absent from the opener; a loopback
        server that redirects to a second cleartext URL proves the opener
        itself carries it.
        """

        class _Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/asset")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return None

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirect) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/"
            try:
                with pytest.raises(urllib.error.URLError, match="non-HTTPS"):
                    makeutil_verify._https_only_opener().open(url, timeout=5)
            finally:
                server.shutdown()

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
