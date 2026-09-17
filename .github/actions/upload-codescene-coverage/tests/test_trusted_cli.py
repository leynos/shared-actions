"""Contracts for the CodeScene CLI manifest, installer, and coverage fixtures."""

from __future__ import annotations

import dataclasses as dc
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import typing as typ
import warnings
import zipfile
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import TracebackType

ACTION = Path(__file__).resolve().parents[1]
SCRIPT = ACTION / "scripts" / "install_cs_coverage.py"
MANIFEST = ACTION / "cli-manifest.json"
FIXTURES = ACTION / "tests" / "fixtures"

sys.path.insert(0, str(SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("install_cs_coverage", SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
installer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = installer
_SPEC.loader.exec_module(installer)


def _manifest(tmp_path: Path, release: dict[str, object]) -> Path:
    """Write one manifest release and return its path."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "releases": [release]}))
    return path


def _release() -> dict[str, object]:
    """Return an independently mutable valid manifest release."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["releases"][0]


@dc.dataclass(frozen=True)
class _ResolutionCase:
    """Capture the manifest and requested platform for one resolution test."""

    manifest: Path
    version: str = "1.0.101"
    runner_os: str = "Linux"
    runner_arch: str = "X64"
    checksum: str = ""


_DEFAULT_RESOLUTION = _ResolutionCase(MANIFEST)


def _resolve(case: _ResolutionCase) -> installer.Release:
    """Resolve a test release through the public request value object."""
    request = installer.ResolutionRequest(
        case.version,
        installer.RunnerPlatform(case.runner_os, case.runner_arch),
        case.checksum,
    )
    return installer.resolve(case.manifest, request)


def _archive(
    path: Path, entries: dict[str, bytes], *, symlink: str | None = None
) -> None:
    """Write a ZIP archive, optionally flagging one entry as a symlink."""
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            if name == symlink:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, content)


def test_manifest_resolves_the_recorded_linux_release() -> None:
    """The committed trust anchor records the known-good immutable archive."""
    resolved = _resolve(_DEFAULT_RESOLUTION)

    assert resolved.build == "ca2b95180eff32b5072e81f20d718d7b747650be"
    assert (
        resolved.archive_sha256
        == "067503dc646c58c2d62c3315b55541e60e0b6343ef6d898650a9e0ae7cde0929"
    )
    assert resolved.archive_url.endswith(f"{resolved.build}.zip")


@pytest.mark.parametrize(
    ("version", "runner_os", "runner_arch"),
    [
        ("latest", "Linux", "X64"),
        ("1.0.102", "Linux", "X64"),
        ("1.0.101", "Windows", "X64"),
    ],
)
def test_unknown_or_unsupported_resolution_fails_closed(
    version: str, runner_os: str, runner_arch: str
) -> None:
    """A floating version, absent pin, or unsupported runner cannot install."""
    with pytest.raises(installer.InstallError):
        _resolve(_ResolutionCase(MANIFEST, version, runner_os, runner_arch))


def test_invalid_manifest_missing_digest_and_conflicting_checksum_fail(
    tmp_path: Path,
) -> None:
    """No malformed trust anchor or caller override can weaken the digest."""
    unreadable = tmp_path / "missing.json"
    with pytest.raises(installer.InstallError):
        _resolve(_ResolutionCase(unreadable))
    release = _release()
    release["archive_sha256"] = ""
    with pytest.raises(installer.InstallError):
        _resolve(_ResolutionCase(_manifest(tmp_path, release)))
    with pytest.raises(installer.InstallError, match="conflicts"):
        _resolve(dc.replace(_DEFAULT_RESOLUTION, checksum="0" * 64))


def test_extract_rejects_unsafe_archive_members(tmp_path: Path) -> None:
    """Traversal, unexpected members, and symlinks never reach the executable path."""
    release = _resolve(_DEFAULT_RESOLUTION)
    archive = tmp_path / "unsafe.zip"
    _archive(
        archive,
        {
            "cs-coverage": b"binary",
            "cs-coverage.sha256": b"digest",
            "cs-coverage.sha256.asc": b"signature",
            "../escape": b"bad",
        },
    )
    with pytest.raises(installer.InstallError):
        installer.extract_cli(archive, release, tmp_path / "cs-coverage")


@pytest.mark.parametrize("unsafe_name", ["../escape", "/escape", r"..\escape"])
def test_extract_rejects_unsafe_expected_archive_paths(
    tmp_path: Path, unsafe_name: str
) -> None:
    """Path validation rejects unsafe names even when the manifest lists them."""
    resolved = _resolve(_DEFAULT_RESOLUTION)
    release = dc.replace(
        resolved,
        archive_members=(*resolved.archive_members, unsafe_name),
    )
    archive = tmp_path / "unsafe-expected.zip"
    _archive(
        archive,
        {
            "cs-coverage": b"binary",
            "cs-coverage.sha256": b"digest",
            "cs-coverage.sha256.asc": b"signature",
            unsafe_name: b"bad",
        },
    )

    with pytest.raises(installer.InstallError, match="unsafe path"):
        installer.extract_cli(archive, release, tmp_path / "cs-coverage")


def test_extract_rejects_duplicate_archive_members(tmp_path: Path) -> None:
    """Duplicate ZIP names cannot hide a substituted executable payload."""
    release = _resolve(_DEFAULT_RESOLUTION)
    archive = tmp_path / "duplicate.zip"
    _archive(
        archive,
        {
            "cs-coverage": b"binary",
            "cs-coverage.sha256": b"digest",
            "cs-coverage.sha256.asc": b"signature",
        },
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name: 'cs-coverage'")
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("cs-coverage", b"substitution")

    with pytest.raises(installer.InstallError):
        installer.extract_cli(archive, release, tmp_path / "cs-coverage")
    _archive(
        archive,
        {
            "cs-coverage": b"binary",
            "cs-coverage.sha256": b"digest",
            "cs-coverage.sha256.asc": b"signature",
        },
        symlink="cs-coverage",
    )
    with pytest.raises(installer.InstallError):
        installer.extract_cli(archive, release, tmp_path / "cs-coverage")


def test_download_digest_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bytes whose hash differs from the manifest never reach extraction."""
    release = _resolve(_DEFAULT_RESOLUTION)

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
            return Response(b"wrong")

    monkeypatch.setattr(installer, "_download_opener", Opener)
    with pytest.raises(installer.InstallError, match="digest"):
        installer.download_verified(release, tmp_path / "archive.zip")


@pytest.mark.parametrize(
    "status",
    [
        301,
        302,
        303,
        307,
        308,
    ],
)
def test_download_rejects_redirects_before_following(status: int) -> None:
    """The downloader rejects every redirect before it can fetch a new URL."""
    handler = installer._RejectRedirect()
    request = installer.urllib.request.Request(
        "https://downloads.codescene.io/archive.zip"
    )

    with pytest.raises(installer.InstallError, match="redirect"):
        handler.http_error_302(request, None, status)


def test_download_opener_requires_tls_1_2_or_newer() -> None:
    """The action never lowers HTTPS transport below TLS 1.2."""
    opener = installer._download_opener()
    https_handler = next(
        handler
        for handler in opener.handlers
        if isinstance(handler, installer.urllib.request.HTTPSHandler)
    )

    assert https_handler._context.minimum_version >= installer.ssl.TLSVersion.TLSv1_2


def test_version_mismatch_and_failure_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache hit must identify as selected, regardless of the host platform."""
    release = _resolve(_DEFAULT_RESOLUTION)
    binary = tmp_path / "cs-coverage"
    wrong_version = "1.0.102"
    expected_build = "ca2b95180eff32b5072e81f20d718d7b747650be"
    results = iter(
        (
            subprocess.CompletedProcess(
                [str(binary), "version"],
                0,
                stdout=f"cs-coverage version {wrong_version} ({expected_build})\n",
                stderr="",
            ),
            subprocess.CompletedProcess([str(binary), "version"], 17),
        )
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return next(results)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(installer.InstallError, match="does not match"):
        installer.verify_version(binary, release)
    with pytest.raises(installer.InstallError, match="17"):
        installer.verify_version(binary, release)

    expected_options: dict[str, object] = {
        "capture_output": True,
        "check": False,
        "env": os.environ | {"CS_DISABLE_VERSION_CHECK": "1"},
        "text": True,
    }
    assert calls == [
        ([str(binary), "version"], expected_options),
        ([str(binary), "version"], expected_options),
    ]


def test_action_keeps_check_failure_status() -> None:
    """The coverage gate's diagnostic branch exits with the original status."""
    action = (ACTION / "action.yml").read_text(encoding="utf-8")
    assert 'exit "$status"' in action


@pytest.mark.parametrize(
    ("fixture", "expected_digest", "generator"),
    [
        (
            FIXTURES / "slipcover-1.0.18-cobertura.xml",
            "7d3891891f5496f0ffe09b79da09c5798e8d06f85be7f204f7caf8e178848259",
            "v1.0.18",
        ),
        (
            FIXTURES / "slipcover-1.1.0-cobertura.xml",
            "48c664cdcae746904d75c142b5a6209410f2e3200aefa361147fa0c4caa139fa",
            "v1.1.0",
        ),
    ],
)
def test_fixture_is_the_immutable_reported_parser_reproducer(
    fixture: Path, expected_digest: str, generator: str
) -> None:
    """Fixtures are byte-exact Slipcover reports behind the 1.0.103 failure."""
    data = fixture.read_bytes()

    assert hashlib.sha256(data).hexdigest() == expected_digest
    assert (
        f"Generated by slipcover: https://github.com/plasma-umass/slipcover/tree/{generator}".encode()
        in data
    )


def test_fixture_attributes_preserve_canonical_report_bytes() -> None:
    """The fixture checkout disables text normalisation for the hashed reports."""
    repository = ACTION.parents[2]
    attributes = (repository / ".gitattributes").read_text(encoding="utf-8")

    for fixture in FIXTURES.glob("slipcover-*-cobertura.xml"):
        path = fixture.relative_to(repository).as_posix()
        assert f"{path} -text" in attributes
