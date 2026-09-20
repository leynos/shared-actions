"""Validate the pinned cargo-nextest release against the real release archive.

``test_install_cargo_nextest.py`` and ``test_install_cargo_nextest_install.py``
check the installer's own logic using synthetic archives whose digests are
derived from that synthetic content, so neither module can notice a pin table
that was updated with the wrong values, and neither ever extracts or runs a
real ``cargo-nextest``. This module closes that gap.

It drives the *real* installation path -- the real archive digest check, the
real extraction, the real executable digest check, and the real atomic
install -- over ``fixtures/cargo-nextest-0.9.145-x86_64-unknown-linux-gnu.tar.gz``,
a byte-exact copy of the official Linux x86_64 GNU release asset for the pinned
version, accompanied by the checksum file the release itself publishes.

Every expectation is written out as a literal in this module and is compared
against the artefact, never sourced from the installer's pin tables. That is
the whole point: a literal can disagree with the installer, and a test that
reads the expected value from the code under test cannot. The digests below
were taken from the release's own published checksum file (verified by
``test_published_checksum_fixture_records_the_pinned_archive_digest``) rather
than from a self-computed value.

Bumping the pin therefore means updating four things together, in one commit:

1. the version in the installer;
2. the archive checksums in the installer, from the release's published
   ``<asset>.sha256`` files;
3. the executable checksums in the installer, computed from those verified
   archives;
4. the literals and the replacement fixture in this module.

``docs/developers-guide.md`` documents that procedure. Nothing here contacts
the network: the download step is redirected to the checked-in archive, so the
suite stays hermetic.
"""

from __future__ import annotations

import ctypes
import dataclasses
import hashlib
import platform
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    import urllib.request
    from types import ModuleType

# Independent expectations for the pinned release. These are literals copied
# from the upstream release, deliberately not lookups against the installer:
# comparing an artefact against the installer's own table would prove only that
# the installer is self-consistent.
_PINNED_VERSION = "0.9.145"
_PINNED_SYSTEM = "Linux"
_PINNED_MACHINE = "x86_64"
_PINNED_TARGET = "x86_64-unknown-linux-gnu"
_PINNED_EXTENSION = "tar.gz"
_PINNED_ARCHIVE_SHA256 = (
    "32aa82416099eb12fffae9cf1a279ad201fecbd3f74826c613e32e9006b29867"
)
_PINNED_EXECUTABLE_SHA256 = (
    "c4d4f4ad7eb50677b568aae324251e8bd6978e9fc6fe97aa1347a2afdd95e59c"
)
_PINNED_ARCHIVE_BYTES = 12_050_698

_PINNED_ARCHIVE_FILENAME = (
    f"cargo-nextest-{_PINNED_VERSION}-{_PINNED_TARGET}.{_PINNED_EXTENSION}"
)
_PINNED_RELEASE_URL = (
    "https://github.com/nextest-rs/nextest/releases/download/"
    f"cargo-nextest-{_PINNED_VERSION}/{_PINNED_ARCHIVE_FILENAME}"
)

_FIXTURE_DIRECTORY = Path(__file__).resolve().parent / "fixtures"
_ARCHIVE_FIXTURE = _FIXTURE_DIRECTORY / _PINNED_ARCHIVE_FILENAME
# The release publishes this beside the archive as
# ``cargo-nextest-<version>-<target>.sha256`` -- the extension is not carried
# over, so it is not ``<archive>.sha256``.
_PUBLISHED_CHECKSUM_FIXTURE = _FIXTURE_DIRECTORY / (
    f"cargo-nextest-{_PINNED_VERSION}-{_PINNED_TARGET}.sha256"
)

# The installer's pin-table attribute names, assembled here so this module's
# own source never contains them verbatim. See
# ``test_expectations_are_independent_of_the_installer_pin_tables``.
_INSTALLER_PIN_TABLE_NAMES = tuple(
    f"CARGO_NEXTEST_{suffix}" for suffix in ("VERSION", "RELEASE_ASSETS", "SHA256")
)


def _host_libc_is_glibc() -> bool:
    """Return whether this host links against glibc, as the fixture requires."""
    try:
        ctypes.CDLL("libc.so.6").gnu_get_libc_version()
    except (OSError, AttributeError):
        return False
    return True


# The fixture is a glibc x86_64 Linux executable, so only the Linux runners can
# run it. The digest and extraction assertions hold on every platform.
_HOST_RUNS_PINNED_BINARY = (
    sys.platform.startswith("linux")
    and platform.machine().lower() in {"x86_64", "amd64"}
    and _host_libc_is_glibc()
)


def _sha256_of(path: Path) -> str:
    """Return the SHA-256 digest of ``path``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_fixture(path: Path) -> Path:
    """Return ``path``, failing loudly when the checked-in fixture is absent.

    Skipping instead would quietly drop this module's coverage, which is the
    one thing it exists to provide.
    """
    assert path.is_file(), f"missing checked-in fixture: {path}"
    return path


def _select_pinned_platform(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the installer's platform detection to the fixture's platform."""
    monkeypatch.setattr(module.platform, "system", lambda: _PINNED_SYSTEM)
    monkeypatch.setattr(module.platform, "machine", lambda: _PINNED_MACHINE)
    monkeypatch.setattr(module, "_is_musl", lambda: False)


@dataclasses.dataclass(frozen=True)
class _InstallOutcome:
    """Capture what one install performed by the helper observed."""

    destination: Path
    requested_urls: tuple[str, ...]
    expected_sha: str


def _install_pinned_release(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cargo_home: Path,
) -> _InstallOutcome:
    """Run the real install path for the pinned platform against the fixture.

    Only the network boundary is replaced: ``urllib.request.urlopen`` is
    redirected to the checked-in archive while recording the URL the installer
    asked for. Download accounting, the archive digest check, extraction, the
    executable digest check, and the atomic install all run for real.
    """
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    _select_pinned_platform(module, monkeypatch)
    archive = _require_fixture(_ARCHIVE_FIXTURE)
    requested: list[str] = []

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> typ.IO[bytes]:
        """Serve the checked-in release archive in place of the network."""
        del timeout
        requested.append(request.full_url)
        return archive.open("rb")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    expected_sha, asset = module._release_for_platform()
    destination = module.install_cargo_nextest(asset, expected_sha)
    return _InstallOutcome(
        destination=destination,
        requested_urls=tuple(requested),
        expected_sha=expected_sha,
    )


def test_published_checksum_fixture_records_the_pinned_archive_digest() -> None:
    """The release's own published checksum records the expected archive digest.

    This is what makes the literal above upstream's value rather than a
    self-computed one, and it pins the digest to the archive filename the
    release published it against.
    """
    recorded = (
        _require_fixture(_PUBLISHED_CHECKSUM_FIXTURE)
        .read_text(encoding="utf-8")
        .split()
    )

    assert recorded == [_PINNED_ARCHIVE_SHA256, _PINNED_ARCHIVE_FILENAME]


def test_release_archive_fixture_matches_the_pinned_expectations() -> None:
    """The checked-in archive is the pinned release asset, byte for byte."""
    archive = _require_fixture(_ARCHIVE_FIXTURE)

    assert archive.stat().st_size == _PINNED_ARCHIVE_BYTES
    assert _sha256_of(archive) == _PINNED_ARCHIVE_SHA256


def test_installer_pin_matches_the_independent_expectations(
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installer resolves exactly the release this module expects.

    Compared in this direction -- installer resolution against literals here --
    so a partial bump names the field that drifted instead of surfacing later
    as an opaque digest mismatch inside an install attempt.
    """
    _select_pinned_platform(install_nextest_module, monkeypatch)

    expected_sha, asset = install_nextest_module._release_for_platform()

    assert asset.filename == _PINNED_ARCHIVE_FILENAME
    assert asset.sha256 == _PINNED_ARCHIVE_SHA256
    assert expected_sha == _PINNED_EXECUTABLE_SHA256


def test_installer_installs_the_pinned_release_archive(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real install path yields the pinned executable from the real archive.

    Covers all five observable requirements at once: the constructed release
    URL, the archive digest, a successful extraction, the extracted executable's
    digest, and the atomic replacement of the destination.
    """
    cargo_home = tmp_path / "cargo-home"

    outcome = _install_pinned_release(install_nextest_module, monkeypatch, cargo_home)
    destination = cargo_home / "bin" / "cargo-nextest"

    assert outcome.requested_urls == (_PINNED_RELEASE_URL,)
    assert outcome.destination == destination
    assert destination.is_file()
    assert _sha256_of(destination) == _PINNED_EXECUTABLE_SHA256
    assert destination.stat().st_mode & 0o111
    assert not destination.with_suffix(f"{destination.suffix}.tmp").exists()


@pytest.mark.skipif(
    not _HOST_RUNS_PINNED_BINARY,
    reason="the pinned archive is a glibc x86_64 Linux executable",
)
def test_installed_executable_reports_the_pinned_version(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed executable runs and reports the pinned release."""
    cargo_home = tmp_path / "cargo-home"

    outcome = _install_pinned_release(install_nextest_module, monkeypatch, cargo_home)
    result = run_plumbum_command(local[str(outcome.destination)]["--version"])

    assert result.returncode == 0, result.stderr
    banner = result.stdout.splitlines()[0].split()
    assert banner[:2] == ["cargo-nextest", _PINNED_VERSION]


def test_expectations_are_independent_of_the_installer_pin_tables() -> None:
    """The literals above must not be replaced by references to the installer.

    Reading an expected value from the code under test would make every
    assertion above vacuous: the test could no longer disagree with the
    installer, so a wrong pin would pass. This test exists so that a future
    "simplification" that aliases the installer's tables fails here instead of
    silently disarming this module.
    """
    source = Path(__file__).read_text(encoding="utf-8")

    for name in _INSTALLER_PIN_TABLE_NAMES:
        assert name not in source, (
            f"{name} must not be referenced from this module; keep the "
            "expectations above as literals"
        )
