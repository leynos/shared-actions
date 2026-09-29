"""`install_makeutil`: the download, verify, and staged-install orchestrator.

Every scenario here injects a fake downloader, so none of it touches the
network. Each failure scenario also asserts that `bin-dir` is left exactly
as it was found - nothing partially installed, nothing left behind - which
is the fail-closed guarantee the packet requires.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

import makeutil_verify
import pytest
from makeutil_errors import DownloadError, StoreError
from makeutil_store import FilesystemBinaryStore
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
        store=FilesystemBinaryStore(target),
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


class TestExistingExecutableSurvivesFailures:
    """A failed install never disturbs a binary already in `bin-dir`."""

    _OLD = b"an older makeutil that must survive\n"

    @pytest.mark.parametrize(
        ("downloader", "expected_digest", "outcome"),
        [
            pytest.param(_fake_downloader(), "0" * 64, DIGEST_MISMATCH, id="digest"),
            pytest.param(
                _fake_downloader(sidecar=f"{'f' * 64}  {_NAME}\n".encode()),
                _DIGEST,
                SIDECAR_MISMATCH,
                id="sidecar",
            ),
        ],
    )
    def test_a_verification_failure_keeps_the_existing_bytes_and_mode(
        self,
        tmp_path: Path,
        downloader: typ.Callable[[str], bytes],
        expected_digest: str,
        outcome: str,
    ) -> None:
        """The old file is neither replaced nor re-moded when a check fails."""
        target = _seed_cache(tmp_path, self._OLD, mode=0o700)

        result = _install(target, downloader, expected_digest)

        assert result.outcome == outcome
        assert target.read_bytes() == self._OLD
        assert target.stat().st_mode & 0o777 == 0o700

    def test_a_download_failure_keeps_the_existing_bytes_and_mode(
        self, tmp_path: Path
    ) -> None:
        """An unreachable release leaves the previous binary in place."""
        target = _seed_cache(tmp_path, self._OLD, mode=0o700)

        def _failing_downloader(_url: str) -> bytes:
            message = "connection refused"
            raise DownloadError(message)

        result = _install(target, _failing_downloader, "0" * 64)

        assert result.outcome == DOWNLOAD_FAILED
        assert target.read_bytes() == self._OLD
        assert target.stat().st_mode & 0o777 == 0o700


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


class _MemoryStore:
    """An in-memory `BinaryStore` that can be told to fail one operation.

    The policy in `install_makeutil` is proved against this, so no test needs
    to patch `Path` or `os` to make storage fail.
    """

    location = Path("/memory/makeutil")

    def __init__(self, data: bytes | None = None, *, fails_on: str = "") -> None:
        self.data = data
        self.is_executable = False
        self._fails_on = fails_on

    def _maybe_fail(self, operation: str) -> None:
        if operation == self._fails_on:
            message = f"simulated {operation} failure"
            raise StoreError(message)

    def digest(self) -> str | None:
        self._maybe_fail("digest")
        return None if self.data is None else sha256_hex(self.data)

    def make_executable(self) -> None:
        self._maybe_fail("make_executable")
        self.is_executable = True

    def install(self, data: bytes) -> None:
        self._maybe_fail("install")
        self.data = data
        self.is_executable = True

    def discard(self) -> None:
        self.data = None


def _install_in(
    store: _MemoryStore, downloader: typ.Callable[[str], bytes]
) -> makeutil_verify.InstallResult:
    """Run `install_makeutil` against an in-memory store."""
    return install_makeutil(
        store=store,
        expected_sha256=_DIGEST,
        asset_urls=_ASSET_URLS,
        downloader=downloader,
    )


class TestPolicyAgainstAnInMemoryStore:
    """Cache reuse, replacement and storage failure, with no filesystem."""

    def test_a_matching_entry_is_reused_without_a_download(self) -> None:
        """A digest match is `cached` and is made executable, never fetched."""
        store = _MemoryStore(_BINARY)

        result = _install_in(store, _unreachable_downloader)

        assert result.outcome == CACHED
        assert result.path == store.location
        assert store.is_executable

    def test_a_stale_entry_is_replaced_by_the_verified_download(self) -> None:
        """A wrong digest is replaced, not trusted."""
        store = _MemoryStore(b"stale bytes")

        result = _install_in(store, _fake_downloader())

        assert result.outcome == INSTALLED
        assert store.data == _BINARY

    @pytest.mark.parametrize("operation", ["digest", "make_executable"])
    def test_an_unusable_entry_is_install_failed(self, operation: str) -> None:
        """A store that cannot read or re-mode the entry is a bounded failure,
        and nothing is downloaded.
        """
        store = _MemoryStore(_BINARY, fails_on=operation)

        result = _install_in(store, _unreachable_downloader)

        assert result.outcome == INSTALL_FAILED
        assert operation in result.message

    def test_a_staging_failure_is_install_failed_and_keeps_the_old_entry(
        self,
    ) -> None:
        """A store that cannot install leaves the previous binary in place."""
        store = _MemoryStore(b"an older makeutil", fails_on="install")

        result = _install_in(store, _fake_downloader())

        assert result.outcome == INSTALL_FAILED
        assert store.data == b"an older makeutil"
