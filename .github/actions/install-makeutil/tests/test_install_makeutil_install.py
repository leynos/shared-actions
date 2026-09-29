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


class TestStagingFailures:
    """Writing or moding the staged file can fail after both digests agree."""

    @pytest.mark.parametrize("failing_step", ["write", "chmod"])
    def test_a_staging_step_failure_is_install_failed_and_leaves_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_step: str
    ) -> None:
        """An `OSError` while writing or `chmod`-ing the staged file becomes a
        bounded `install-failed` result, and the staged temporary file is
        removed rather than left beside the target.
        """
        target = tmp_path / "bin" / "makeutil"
        real_fdopen = os.fdopen

        class _FailingHandle:
            def __init__(self, handle: typ.IO[bytes]) -> None:
                self._handle = handle

            def __enter__(self) -> typ.Self:
                return self

            def __exit__(self, *_exc_info: object) -> None:
                self._handle.close()

            def write(self, _data: bytes) -> int:
                message = "simulated write failure"
                raise OSError(message)

        def _failing_chmod(self: Path, *_args: object) -> typ.NoReturn:
            message = "simulated chmod failure"
            raise OSError(message)

        if failing_step == "write":
            monkeypatch.setattr(
                makeutil_verify.os,
                "fdopen",
                lambda fd, mode: _FailingHandle(real_fdopen(fd, mode)),
            )
        else:
            monkeypatch.setattr(makeutil_verify.Path, "chmod", _failing_chmod)

        result = _install(target, _fake_downloader())

        assert result.outcome == INSTALL_FAILED
        assert list(target.parent.iterdir()) == []

    def test_a_bin_dir_that_cannot_be_created_is_install_failed(
        self, tmp_path: Path
    ) -> None:
        """`resolve` no longer makes `bin-dir`, so the install step must report
        a directory it cannot create - here, one beneath a regular file - as a
        bounded result rather than a traceback.
        """
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")

        result = _install(blocker / "bin" / "makeutil", _fake_downloader())

        assert result.outcome == INSTALL_FAILED


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

    @pytest.mark.parametrize("failing_call", ["read_bytes", "chmod"])
    def test_an_unusable_cache_entry_is_reported_as_install_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_call: str
    ) -> None:
        """An `OSError` reading or `chmod`-ing the cached file becomes a
        bounded `install-failed` result rather than an uncaught traceback.
        """
        target = _seed_cache(tmp_path, _BINARY)

        def _failing(self: Path, *_args: object) -> typ.NoReturn:
            message = "simulated cache failure"
            raise OSError(message)

        monkeypatch.setattr(makeutil_verify.Path, failing_call, _failing)

        result = _install(target, _unreachable_downloader)

        assert result.outcome == INSTALL_FAILED
        assert "cached" in result.message

    def test_a_cached_file_with_a_wrong_digest_is_reinstalled(
        self, tmp_path: Path
    ) -> None:
        """A stale or tampered cache entry self-heals rather than failing."""
        target = _seed_cache(tmp_path, b"stale bytes from a previous release")

        result = _install(target, _fake_downloader())

        assert result.outcome == INSTALLED
        assert target.read_bytes() == _BINARY
