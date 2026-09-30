"""Download, verify and stage-install makeutil's release binary.

Both the pinned digest table entry and the release's own `.sha256` sidecar
must agree with the downloaded bytes before anything is written to the
target path. The download function is injected so tests exercise every
branch - a table mismatch, a sidecar mismatch, a download failure, a stale
cache - with no network access.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import http.client
import re
import typing as typ
import urllib.error
import urllib.request

from makeutil_errors import DownloadError, MakeutilError, SidecarError, StoreError

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc
    from pathlib import Path

    from makeutil_store import BinaryStore

#: Bounded, so a hung connection fails the job rather than holding a runner.
DOWNLOAD_TIMEOUT_SECONDS = 30

#: A static Rust binary is a few megabytes; this is generous headroom rather
#: than a measured size, chosen so a redirected or compromised endpoint
#: cannot exhaust runner disk before the digest check gets to reject it.
_MAX_ASSET_BYTES = 50 * 1024 * 1024

#: A sidecar body is exactly `<64 lowercase hex>  <name>`: sha256sum's
#: two-space, binary-mode format.
_SIDECAR_LINE_RE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>\S.*)$")

#: The bounded vocabulary `install_makeutil.py`'s `install-makeutil.result`
#: metric ranges over, for the outcomes this module decides.
CACHED = "cached"
INSTALLED = "installed"
DIGEST_MISMATCH = "digest-mismatch"
SIDECAR_MISMATCH = "sidecar-mismatch"
DOWNLOAD_FAILED = "download-failed"
INSTALL_FAILED = "install-failed"

#: Cache state of a run, reported beside its result. `hit` is a restored
#: entry that verified and was reused, `stale` a restored entry that was
#: rejected or unusable, and `miss` no restored entry at all.
CACHE_HIT = "hit"
CACHE_MISS = "miss"
CACHE_STALE = "stale"

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    Downloader = cabc.Callable[[str], bytes]
else:
    Downloader = typ.Callable


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def parse_sidecar(text: str, expected_name: str) -> str:
    """Parse a `<64 hex>  <name>` sidecar body and return its digest.

    Parameters
    ----------
    text : str
        The sidecar file's contents.
    expected_name : str
        The asset file name the sidecar must name.

    Returns
    -------
    str
        The lowercase hex digest the sidecar carries.

    Raises
    ------
    SidecarError
        If the body is not exactly one well-formed `sha256sum`-style line,
        or the line names a different file than the one that was downloaded.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        msg = f"sidecar must contain exactly one line, got {len(lines)}"
        raise SidecarError(msg)
    match = _SIDECAR_LINE_RE.match(lines[0])
    if match is None:
        msg = f"sidecar line is not '<64 hex>  <name>': {lines[0]!r}"
        raise SidecarError(msg)
    name = match.group("name")
    if name != expected_name:
        msg = f"sidecar names {name!r}, expected {expected_name!r}"
        raise SidecarError(msg)
    return match.group("digest")


def _https_only_opener(
    https_handler: urllib.request.BaseHandler | None = None,
) -> urllib.request.OpenerDirector:
    """Build an opener that can speak HTTPS and nothing else.

    `urlopen` follows a redirect without re-checking the scheme, so a release
    asset answering 302 to `http://` or `ftp://` would be fetched in the
    clear. GitHub redirects release assets to a CDN, so redirects stay
    enabled; the opener instead carries no handler for any other scheme, and
    a hop to one fails with an unknown-URL-type error before any request.

    `https_handler` exists so a test can serve canned HTTPS responses,
    redirects included, without a live TLS server; production passes none.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        https_handler or urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def default_downloader(url: str) -> bytes:
    """Fetch `url` over HTTPS only, bounded by a timeout and a size cap.

    Every redirect hop must also be HTTPS.

    Raises
    ------
    DownloadError
        If the URL is not HTTPS, the request fails, or the response exceeds
        the size cap.
    """
    if not url.startswith("https://"):
        msg = f"refusing a non-HTTPS URL: {url}"
        raise DownloadError(msg)
    request = urllib.request.Request(url)  # noqa: S310 - fixed https origin
    try:
        with _https_only_opener().open(
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            data = response.read(_MAX_ASSET_BYTES + 1)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        msg = f"could not download {url}: {error}"
        raise DownloadError(msg) from error
    if len(data) > _MAX_ASSET_BYTES:
        msg = f"asset at {url} exceeds the {_MAX_ASSET_BYTES}-byte limit"
        raise DownloadError(msg)
    return data


@dc.dataclass(slots=True, frozen=True)
class InstallResult:
    """The outcome of one `install_makeutil` attempt."""

    outcome: str
    message: str = ""
    path: Path | None = None


@dc.dataclass(slots=True, frozen=True)
class AssetUrls:
    """The release asset URL and its published `.sha256` sidecar URL.

    Grouped so `install_makeutil` takes one URL argument instead of two; the
    pair is always resolved together and never used independently.
    """

    binary: str
    sidecar: str


class _InstallStoppedError(Exception):
    """Carry the bounded result that stops an install before it writes.

    Each verification step raises this rather than returning a sentinel, so
    `install_makeutil` reads as the ordered sequence of checks it performs.
    """

    def __init__(self, result: InstallResult) -> None:
        super().__init__(result.message)
        self.result = result


def _download(downloader: Downloader, url: str) -> bytes:
    """Fetch `url`, stopping the install as `download-failed` on error."""
    try:
        return downloader(url)
    except MakeutilError as error:
        raise _InstallStoppedError(
            InstallResult(DOWNLOAD_FAILED, message=str(error))
        ) from error


def _require_table_digest(binary: bytes, expected_sha256: str) -> None:
    """Stop the install as `digest-mismatch` unless `binary` matches the table."""
    actual = sha256_hex(binary)
    if actual != expected_sha256:
        message = (
            f"downloaded digest {actual} does not match the pinned table "
            f"digest {expected_sha256}"
        )
        raise _InstallStoppedError(InstallResult(DIGEST_MISMATCH, message=message))


def _sidecar_digest(sidecar_bytes: bytes, expected_name: str) -> str:
    """Return the sidecar's digest, stopping as `sidecar-mismatch` if malformed."""
    try:
        return parse_sidecar(sidecar_bytes.decode("ascii"), expected_name)
    except UnicodeDecodeError as error:
        message = f"sidecar is not ASCII: {error}"
        raise _InstallStoppedError(
            InstallResult(SIDECAR_MISMATCH, message=message)
        ) from error
    except SidecarError as error:
        raise _InstallStoppedError(
            InstallResult(SIDECAR_MISMATCH, message=str(error))
        ) from error


def _require_sidecar_digest(sidecar_digest: str, expected_sha256: str) -> None:
    """Stop the install as `sidecar-mismatch` unless the sidecar agrees."""
    if sidecar_digest != expected_sha256:
        message = (
            f"sidecar digest {sidecar_digest} does not match the pinned "
            f"table digest {expected_sha256}"
        )
        raise _InstallStoppedError(InstallResult(SIDECAR_MISMATCH, message=message))


def _finish_install(binary: bytes, store: BinaryStore) -> InstallResult:
    """Install `binary` through `store`, or report a bounded `install-failed`.

    Staging can fail - a full disk, a permission problem - after both digests
    already agreed; that becomes this module's own bounded outcome rather than
    an uncaught traceback.
    """
    try:
        store.install(binary)
    except StoreError as error:
        return InstallResult(INSTALL_FAILED, message=str(error))
    return InstallResult(INSTALLED, path=store.location)


def _reuse_cached_binary(
    store: BinaryStore, expected_sha256: str
) -> InstallResult | None:
    """Return a `cached` result when the stored binary is reusable.

    `None` means there is nothing to reuse and the caller should download. A
    digest match proves the bytes are right, not that the mode survived
    whatever placed them there - a cache restore does not preserve the
    executable bit - so the mode is set here. A store that cannot read or
    re-mode the entry yields a bounded `install-failed` result.
    """
    try:
        if store.digest() != expected_sha256:
            return None
        store.make_executable()
    except StoreError as error:
        return InstallResult(INSTALL_FAILED, message=str(error))
    return InstallResult(CACHED, path=store.location)


def install_makeutil(
    *,
    store: BinaryStore,
    expected_sha256: str,
    asset_urls: AssetUrls,
    downloader: Downloader = default_downloader,
) -> InstallResult:
    """Install makeutil's verified binary, or report why installation stopped.

    Both the pinned digest table entry (`expected_sha256`) and the release's
    own `.sha256` sidecar must agree with the downloaded bytes before
    anything is written to `store`. A binary already in `store` is trusted
    only when it already carries `expected_sha256`; otherwise it is replaced,
    which is what makes a cache hit with a stale digest self-heal rather than
    fail silently.

    Parameters
    ----------
    store : BinaryStore
        Where the verified binary is installed; injected so the policy here
        touches no filesystem of its own.
    expected_sha256 : str
        The digest the download must match; ordinarily the digest table's
        entry, but overridable for a test that tampers with it.
    asset_urls : AssetUrls
        The release asset URL and its published `.sha256` sidecar URL.
    downloader : Downloader
        Injected so tests need no network.

    Returns
    -------
    InstallResult
        `outcome` is one of the bounded metric values this module declares.
    """
    cached = _reuse_cached_binary(store, expected_sha256)
    if cached is not None:
        return cached

    expected_name = asset_urls.binary.rsplit("/", 1)[-1]
    try:
        binary = _download(downloader, asset_urls.binary)
        _require_table_digest(binary, expected_sha256)
        sidecar_bytes = _download(downloader, asset_urls.sidecar)
        _require_sidecar_digest(
            _sidecar_digest(sidecar_bytes, expected_name), expected_sha256
        )
    except _InstallStoppedError as stopped:
        return stopped.result

    return _finish_install(binary, store)
