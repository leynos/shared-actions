"""Security and portability contracts for the trusted CodeScene CLI installer."""

from __future__ import annotations

import dataclasses as dc
import hashlib
import importlib.util
import io
import sys
import threading
import typing as typ
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import TracebackType
from hypothesis import given
from hypothesis import strategies as st

ACTION = Path(__file__).resolve().parents[1]
SCRIPT = ACTION / "scripts" / "install_cs_coverage.py"
MANIFEST = ACTION / "cli-manifest.json"

sys.path.insert(0, str(SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("install_cs_coverage_security", SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
installer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = installer
_SPEC.loader.exec_module(installer)


def _release() -> installer.Release:
    """Resolve the committed Linux release used by installer tests."""
    request = installer.ResolutionRequest(
        "1.0.101", installer.RunnerPlatform("Linux", "X64")
    )
    return installer.resolve(MANIFEST, request)


def _archive(path: Path, release: installer.Release) -> bytes:
    """Create the exact trusted ZIP member set and return its binary bytes."""
    binary = b"trusted cs-coverage"
    entries = {
        release.member: binary,
        "cs-coverage.sha256": b"digest",
        "cs-coverage.sha256.asc": b"signature",
    }
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return binary


def test_extract_cli_installs_only_the_trusted_member(tmp_path: Path) -> None:
    """A matching archive installs the recorded executable bytes."""
    release = _release()
    archive = tmp_path / "cs-coverage.zip"
    expected = _archive(archive, release)
    destination = tmp_path / "bin" / "cs-coverage"

    installer.extract_cli(archive, release, destination)

    assert destination.read_bytes() == expected


class _RedirectHandler(BaseHTTPRequestHandler):
    """Serve one redirect while recording every request path."""

    paths: typ.ClassVar[list[str]] = []

    def do_GET(self) -> None:
        """Return a redirect that the trusted opener must not follow."""
        type(self).paths.append(self.path)
        self.send_response(302)
        self.send_header("Location", "/target")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib callback API.
        """Keep the test server's request log out of pytest output."""


def test_opener_rejects_redirect_before_following() -> None:
    """The installed redirect handler wins over urllib's default handler."""
    _RedirectHandler.paths.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/redirect"
        request = installer.urllib.request.Request(url)
        with pytest.raises(installer.InstallError, match="redirect"):
            installer._download_opener().open(request, timeout=1)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert _RedirectHandler.paths == ["/redirect"]


def test_download_verified_accepts_the_approved_final_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deterministic response at the manifest URL is downloaded and verified."""
    release = _release()
    content = b"approved archive"

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            _ = (exc_type, exc_val, exc_tb)
            self.close()

        def geturl(self) -> str:
            return release.archive_url

    class Opener:
        def open(self, *_: object, **__: object) -> Response:
            return Response(content)

    monkeypatch.setattr(installer, "_download_opener", Opener)
    target = tmp_path / "archive.zip"
    installer.download_verified(
        dc.replace(release, archive_sha256=hashlib.sha256(content).hexdigest()),
        target,
    )

    assert target.read_bytes() == content


def test_version_startup_error_is_an_install_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing or non-executable binaries fail through the installer taxonomy."""
    startup_error = "not executable"

    def fail(*_: object, **__: object) -> None:
        raise OSError(startup_error)

    monkeypatch.setattr(installer.subprocess, "run", fail)

    with pytest.raises(installer.InstallError, match="cannot start"):
        installer.verify_version(Path("cs-coverage"), _release())


_SAFE_NAMES = st.from_regex(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,24}", fullmatch=True)


@given(_SAFE_NAMES)
def test_safe_member_accepts_generated_single_file_names(name: str) -> None:
    """Ordinary bounded file names are accepted by archive path validation."""
    assert installer.safe_member(name)


@pytest.mark.parametrize(
    "name", ["/absolute", "../parent", "nested/file", r"nested\\file", "\x00"]
)
def test_safe_member_rejects_unsafe_path_forms(name: str) -> None:
    """Absolute, parent, nested, backslash, and NUL names cannot be extracted."""
    assert not installer.safe_member(name)
