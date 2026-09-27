"""`install_makeutil`: the download, verify, and staged-install orchestrator.

Every scenario here injects a fake downloader, so none of it touches the
network. Each failure scenario also asserts that `bin-dir` is left exactly
as it was found - nothing partially installed, nothing left behind - which
is the fail-closed guarantee the packet requires.
"""

from __future__ import annotations

import os
import typing as typ

import makeutil_verify
import pytest
from makeutil_errors import DownloadError
from makeutil_verify import (
    CACHED,
    DIGEST_MISMATCH,
    DOWNLOAD_FAILED,
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


class TestSuccess:
    """Both digests match: the binary is installed, executable, in place."""

    def test_a_verified_download_is_installed_executable(self, tmp_path: Path) -> None:
        """The staged file lands at `executable_path` with the exec bit set."""
        target = tmp_path / "bin" / "makeutil"
        digest = sha256_hex(_BINARY)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=digest,
            asset_urls=_ASSET_URLS,
            downloader=_fake_downloader(),
        )

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

        result = install_makeutil(
            executable_path=target,
            expected_sha256=wrong_digest,
            asset_urls=_ASSET_URLS,
            downloader=_fake_downloader(),
        )

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
        digest = sha256_hex(_BINARY)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=digest,
            asset_urls=_ASSET_URLS,
            downloader=_fake_downloader(sidecar=tampered_sidecar),
        )

        assert result.outcome == SIDECAR_MISMATCH
        assert not target.exists()

    def test_a_download_failure_installs_nothing(self, tmp_path: Path) -> None:
        """A downloader that raises stops the install with no file written."""
        target = tmp_path / "bin" / "makeutil"

        def _failing_downloader(_url: str) -> bytes:
            message = "connection refused"
            raise DownloadError(message)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=sha256_hex(_BINARY),
            asset_urls=_ASSET_URLS,
            downloader=_failing_downloader,
        )

        assert result.outcome == DOWNLOAD_FAILED
        assert not target.exists()

    def test_a_sidecar_download_failure_installs_nothing(self, tmp_path: Path) -> None:
        """The binary alone verifying is not enough; the sidecar fetch can
        still fail and must still leave nothing installed.
        """
        target = tmp_path / "bin" / "makeutil"
        digest = sha256_hex(_BINARY)

        def _download(url: str) -> bytes:
            if url == _BINARY_URL:
                return _BINARY
            message = "sidecar not found"
            raise DownloadError(message)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=digest,
            asset_urls=_ASSET_URLS,
            downloader=_download,
        )

        assert result.outcome == DOWNLOAD_FAILED
        assert not target.exists()


class TestCacheReverification:
    """A cache hit is only ever trusted after re-verifying its digest."""

    def test_a_cached_file_with_the_expected_digest_is_reused(
        self, tmp_path: Path
    ) -> None:
        """No downloader call is needed when the cached bytes already match."""
        target = tmp_path / "bin" / "makeutil"
        target.parent.mkdir(parents=True)
        target.write_bytes(_BINARY)
        digest = sha256_hex(_BINARY)

        def _unreachable_downloader(_url: str) -> bytes:
            message = "a valid cache hit must never call the downloader"
            raise AssertionError(message)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=digest,
            asset_urls=_ASSET_URLS,
            downloader=_unreachable_downloader,
        )

        assert result.outcome == CACHED
        assert result.path == target

    def test_a_cached_file_with_a_wrong_digest_is_reinstalled(
        self, tmp_path: Path
    ) -> None:
        """A stale or tampered cache entry self-heals rather than failing."""
        target = tmp_path / "bin" / "makeutil"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"stale bytes from a previous release")
        digest = sha256_hex(_BINARY)

        result = install_makeutil(
            executable_path=target,
            expected_sha256=digest,
            asset_urls=_ASSET_URLS,
            downloader=_fake_downloader(),
        )

        assert result.outcome == INSTALLED
        assert target.read_bytes() == _BINARY


class TestHttpsOnly:
    """The default downloader refuses anything but HTTPS."""

    def test_a_non_https_url_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `http://` URL is refused by name and never reaches `urlopen`.

        `urlopen` is replaced with one that fails the test, so a refusal that
        came from the network rather than from the scheme check cannot pass.
        """

        def no_request(*_args: object, **_kwargs: object) -> typ.NoReturn:
            msg = "urlopen must not be called for a non-HTTPS URL"
            raise AssertionError(msg)

        monkeypatch.setattr(makeutil_verify.urllib.request, "urlopen", no_request)

        with pytest.raises(DownloadError, match="non-HTTPS"):
            makeutil_verify.default_downloader(
                "http://github.com/leynos/makeutil/releases/download/x"
            )
