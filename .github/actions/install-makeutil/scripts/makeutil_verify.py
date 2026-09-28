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
import os
import re
import tempfile
import typing as typ
import urllib.error
import urllib.request
from pathlib import Path

from makeutil_errors import DownloadError, MakeutilError, SidecarError

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

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


def default_downloader(url: str) -> bytes:
    """Fetch `url` over HTTPS only, bounded by a timeout and a size cap.

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
        with urllib.request.urlopen(  # noqa: S310 - fixed https origin
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


def stage_and_install(data: bytes, target: Path) -> None:
    """Write `data` to a staged file and move it into place atomically.

    The staged file is created beside `target`, given the executable bit,
    and moved into place with `Path.replace`, which is atomic on the same
    filesystem. A failure at any point removes the staged file rather than
    leaving a partial one where a caller might find it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}."
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        staged.chmod(0o755)
        staged.replace(target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


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


def _cached_digest_matches(target: Path, expected_sha256: str) -> bool:
    """Return whether a file already at `target` carries `expected_sha256`."""
    if not target.is_file():
        return False
    return sha256_hex(target.read_bytes()) == expected_sha256


def _finish_install(binary: bytes, executable_path: Path) -> InstallResult:
    """Stage `binary` into place, or report a bounded `install-failed` result.

    Writing, `chmod`-ing or moving the staged file can fail - a full disk, a
    permission problem - after both digests already agreed; that must become
    this module's own bounded outcome rather than an uncaught traceback, and
    `stage_and_install` has already cleaned up any staged temporary file by
    the time this returns.
    """
    try:
        stage_and_install(binary, executable_path)
    except OSError as error:
        message = f"could not install to {executable_path}: {error}"
        return InstallResult(INSTALL_FAILED, message=message)
    return InstallResult(INSTALLED, path=executable_path)


def install_makeutil(
    *,
    executable_path: Path,
    expected_sha256: str,
    asset_urls: AssetUrls,
    downloader: Downloader = default_downloader,
) -> InstallResult:
    """Install makeutil's verified binary, or report why installation stopped.

    Both the pinned digest table entry (`expected_sha256`) and the release's
    own `.sha256` sidecar must agree with the downloaded bytes before
    anything is written to `executable_path`. A file already at
    `executable_path` is trusted only when it already carries
    `expected_sha256`; otherwise it is replaced, which is what makes a cache
    hit with a stale digest self-heal rather than fail silently.

    Parameters
    ----------
    executable_path : Path
        Where the verified binary is installed.
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
    if _cached_digest_matches(executable_path, expected_sha256):
        # A digest match proves the bytes are right, not that the mode
        # survived whatever placed them there; a cache restore in
        # particular does not preserve the executable bit.
        executable_path.chmod(0o755)
        return InstallResult(CACHED, path=executable_path)

    try:
        binary = downloader(asset_urls.binary)
    except MakeutilError as error:
        return InstallResult(DOWNLOAD_FAILED, message=str(error))

    actual = sha256_hex(binary)
    if actual != expected_sha256:
        message = (
            f"downloaded digest {actual} does not match the pinned table "
            f"digest {expected_sha256}"
        )
        return InstallResult(DIGEST_MISMATCH, message=message)

    try:
        sidecar_bytes = downloader(asset_urls.sidecar)
    except MakeutilError as error:
        return InstallResult(DOWNLOAD_FAILED, message=str(error))

    try:
        sidecar_text = sidecar_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        return InstallResult(SIDECAR_MISMATCH, message=f"sidecar is not ASCII: {error}")

    expected_name = asset_urls.binary.rsplit("/", 1)[-1]
    try:
        sidecar_digest = parse_sidecar(sidecar_text, expected_name)
    except SidecarError as error:
        return InstallResult(SIDECAR_MISMATCH, message=str(error))

    if sidecar_digest != expected_sha256:
        message = (
            f"sidecar digest {sidecar_digest} does not match the pinned "
            f"table digest {expected_sha256}"
        )
        return InstallResult(SIDECAR_MISMATCH, message=message)

    return _finish_install(binary, executable_path)
