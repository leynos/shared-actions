"""Verify the cargo-nextest install, extraction, and metric paths.

Split from ``test_install_cargo_nextest.py``, which keeps the platform and
release resolution tests, so neither module exceeds the file length the
code-health rules allow. The tests and their identifiers are unchanged by the
move.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import tarfile
import typing as typ
import zipfile

import pytest
from _coverage_test_support import _exit_code

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


_NEXTEST_ARCHIVE_PAYLOAD = b"nextest-archive"
_NEXTEST_BINARY_PAYLOAD = b"nextest-binary"
_NEXTEST_SENTINEL = b"previously installed cargo-nextest"
_NEXTEST_FAILURE_MODES = (
    "download",
    "archive-digest",
    "extract",
    "binary-digest",
)


@dataclasses.dataclass(frozen=True)
class _NextestInstallOutcome:
    """Capture the observable result of one install attempt."""

    exit_code: int | None
    destination_bytes: bytes | None
    temporary_exists: bool
    extracted: bool


def _nextest_asset(module: ModuleType, digest: str) -> object:
    """Return a Linux release asset pinned to ``digest``."""
    return module.ReleaseAsset("x86_64-unknown-linux-gnu", "tar.gz", digest)


@dataclasses.dataclass(frozen=True)
class _NextestInstallFixture:
    """Bundle the sentinel destination paths and the pinned fixture digests."""

    destination: Path
    temporary: Path
    archive_digest: str
    binary_digest: str


def _seed_nextest_sentinel(cargo_home: Path) -> _NextestInstallFixture:
    """Write the sentinel destination binary and compute the fixture digests."""
    destination = cargo_home / "bin" / "cargo-nextest"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_NEXTEST_SENTINEL)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    return _NextestInstallFixture(
        destination=destination,
        temporary=temporary,
        archive_digest=hashlib.sha256(_NEXTEST_ARCHIVE_PAYLOAD).hexdigest(),
        binary_digest=hashlib.sha256(_NEXTEST_BINARY_PAYLOAD).hexdigest(),
    )


class _NextestInstallStubs:
    """Stub the download and extract steps for one failure mode."""

    def __init__(self, module: ModuleType, failure: str | None) -> None:
        self._module = module
        self._failure = failure
        self.extracted = False

    def download(self, _asset: object, target: Path) -> None:
        """Write the fixture archive, or raise if ``failure`` is ``download``."""
        if self._failure == "download":
            raise self._module.typer.Exit(1)
        target.write_bytes(_NEXTEST_ARCHIVE_PAYLOAD)

    def extract(self, _archive: Path, _asset: object, target: Path) -> None:
        """Write the fixture binary, or raise if ``failure`` is ``extract``."""
        self.extracted = True
        if self._failure == "extract":
            message = "cargo-nextest missing from archive"
            raise ValueError(message)
        payload = (
            b"tampered" if self._failure == "binary-digest" else _NEXTEST_BINARY_PAYLOAD
        )
        target.write_bytes(payload)


@dataclasses.dataclass(frozen=True)
class _NextestAttemptPlan:
    """Describe one install attempt: the release asset and how to run it."""

    asset: object
    binary_digest: str
    entry_point: str
    failure: str | None


def _invoke_nextest_install(
    module: ModuleType,
    cargo_home: Path,
    plan: _NextestAttemptPlan,
    stubs: _NextestInstallStubs,
) -> int | None:
    """Run the chosen entry point under stubbed I/O and return its exit code.

    ``plan.entry_point`` selects ``install_cargo_nextest`` directly or
    ``main``, which first resolves the platform release. The resolution
    stub reflects the destination file's real state, so ``main``'s
    post-install re-resolution check sees the binary once it exists,
    matching the sentinel-then-real-binary lifecycle the fixture drives.
    """
    destination = cargo_home / "bin" / "cargo-nextest"

    def resolve_destination_if_present() -> Path | None:
        """Return ``destination`` when it exists on disk, else ``None``."""
        return destination if destination.exists() else None

    exit_code: int | None = None
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("CARGO_HOME", str(cargo_home))
        patcher.setattr(module, "_download_archive", stubs.download)
        patcher.setattr(module, "_extract_binary", stubs.extract)
        if plan.entry_point == "main":
            patcher.setattr(
                module, "_resolve_nextest_binary", resolve_destination_if_present
            )
            patcher.setattr(
                module,
                "_release_for_platform",
                lambda: (plan.binary_digest, plan.asset),
            )
            invoke = module.main
        else:

            def invoke() -> None:
                module.install_cargo_nextest(plan.asset, plan.binary_digest)

        if plan.failure is None:
            invoke()
        else:
            with pytest.raises(module.typer.Exit) as excinfo:
                invoke()
            exit_code = _exit_code(excinfo.value)

    return exit_code


def _attempt_nextest_install(
    module: ModuleType,
    cargo_home: Path,
    failure: str | None,
    entry_point: str = "install",
) -> _NextestInstallOutcome:
    """Install cargo-nextest with stubbed I/O and report what changed on disk.

    ``entry_point`` selects ``install_cargo_nextest`` directly or ``main``,
    which first resolves the platform release and finds the sentinel binary
    seeded below, so its version check fails and installation proceeds.
    """
    fixture = _seed_nextest_sentinel(cargo_home)
    asset = _nextest_asset(
        module,
        "0" * 64 if failure == "archive-digest" else fixture.archive_digest,
    )
    stubs = _NextestInstallStubs(module, failure)
    plan = _NextestAttemptPlan(
        asset=asset,
        binary_digest=fixture.binary_digest,
        entry_point=entry_point,
        failure=failure,
    )
    exit_code = _invoke_nextest_install(module, cargo_home, plan, stubs)

    return _NextestInstallOutcome(
        exit_code=exit_code,
        destination_bytes=(
            fixture.destination.read_bytes() if fixture.destination.exists() else None
        ),
        temporary_exists=fixture.temporary.exists(),
        extracted=stubs.extracted,
    )


def _assert_install_failed_preserving_sentinel(
    outcome: _NextestInstallOutcome,
) -> None:
    """Assert a failed install left the sentinel destination untouched."""
    assert outcome.exit_code == 1
    assert outcome.destination_bytes == _NEXTEST_SENTINEL
    assert not outcome.temporary_exists


def test_install_nextest_installs_official_release(
    tmp_path: Path,
    install_nextest_module: ModuleType,
) -> None:
    """``main`` verifies the official archive before installing its binary."""
    cargo_home = tmp_path / "cargo-home"

    outcome = _attempt_nextest_install(
        install_nextest_module,
        cargo_home,
        None,
        entry_point="main",
    )

    assert outcome.exit_code is None
    assert outcome.destination_bytes == _NEXTEST_BINARY_PAYLOAD
    assert not outcome.temporary_exists
    binary = cargo_home / "bin" / "cargo-nextest"
    assert binary.stat().st_mode & 0o111


@pytest.mark.parametrize(
    ("failure", "expect_extracted"),
    [
        pytest.param("archive-digest", False, id="archive-digest"),
        pytest.param("binary-digest", True, id="binary-digest"),
    ],
)
def test_install_nextest_digest_mismatch_preserves_destination(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    failure: str,
    *,
    expect_extracted: bool,
) -> None:
    """A mismatched digest leaves the installed binary intact.

    The archive digest gates extraction, so a bad archive is never opened,
    whereas a bad executable digest is only detectable after extraction.
    """
    outcome = _attempt_nextest_install(
        install_nextest_module,
        tmp_path / "cargo-home",
        failure,
    )

    _assert_install_failed_preserving_sentinel(outcome)
    assert outcome.extracted is expect_extracted


def _write_tar_archive(
    path: Path,
    members: dict[str, bytes | None],
) -> None:
    """Write a gzip tar archive; a ``None`` payload records a directory member."""
    with tarfile.open(path, "w:gz") as package:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            if payload is None:
                info.type = tarfile.DIRTYPE
                package.addfile(info)
                continue
            info.size = len(payload)
            package.addfile(info, io.BytesIO(payload))


def _write_zip_archive(path: Path, members: dict[str, bytes]) -> None:
    """Write a zip archive containing each member's bytes payload."""
    with zipfile.ZipFile(path, "w") as package:
        for name, payload in members.items():
            package.writestr(name, payload)


def _write_archive(
    path: Path,
    members: dict[str, bytes | None],
    *,
    archive_format: typ.Literal["tar", "zip"],
) -> None:
    """Write a tar.gz or zip archive with ``members``, dispatching by format."""
    if archive_format == "zip":
        assert all(payload is not None for payload in members.values())
        _write_zip_archive(path, typ.cast("dict[str, bytes]", members))
    else:
        _write_tar_archive(path, members)


def _windows_asset(module: ModuleType) -> object:
    """Return a Windows release asset that selects the zip extraction path."""
    return module.ReleaseAsset("x86_64-pc-windows-msvc", "zip", "0" * 64)


@dataclasses.dataclass(frozen=True)
class _ReadArchiveCase:
    """Describe one real-archive extraction case that must succeed."""

    archive_format: str
    filename: str
    executable: str
    destination_name: str
    windows: bool


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _ReadArchiveCase(
                "tar",
                "cargo-nextest.tar.gz",
                "pkg/cargo-nextest",
                "extracted",
                windows=False,
            ),
            id="tar",
        ),
        pytest.param(
            _ReadArchiveCase(
                "zip",
                "cargo-nextest.zip",
                "pkg/cargo-nextest.exe",
                "extracted.exe",
                windows=True,
            ),
            id="zip",
        ),
    ],
)
def test_extract_binary_reads_real_archive(
    case: _ReadArchiveCase,
    tmp_path: Path,
    install_nextest_module: ModuleType,
) -> None:
    """A real archive of either format yields the cargo-nextest executable."""
    archive = tmp_path / case.filename
    _write_archive(
        archive,
        {"pkg/README": b"docs", case.executable: _NEXTEST_BINARY_PAYLOAD},
        archive_format=case.archive_format,
    )
    destination = tmp_path / case.destination_name
    asset = (
        _windows_asset(install_nextest_module)
        if case.windows
        else _nextest_asset(install_nextest_module, "0" * 64)
    )

    install_nextest_module._extract_binary(archive, asset, destination)

    assert destination.read_bytes() == _NEXTEST_BINARY_PAYLOAD


@dataclasses.dataclass(frozen=True)
class _RejectArchiveCase:
    """Describe one archive missing its expected executable member."""

    archive_format: typ.Literal["tar", "zip"]
    filename: str
    members: dict[str, bytes | None]
    destination_name: str
    expected_match: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _RejectArchiveCase(
                "tar",
                "cargo-nextest.tar.gz",
                {"pkg/README": b"docs"},
                "extracted",
                "cargo-nextest missing from",
            ),
            id="tar-missing-executable",
        ),
        pytest.param(
            _RejectArchiveCase(
                "tar",
                "cargo-nextest.tar.gz",
                {"pkg/cargo-nextest": None},
                "extracted",
                "is not a file in",
            ),
            id="tar-directory-member",
        ),
        pytest.param(
            _RejectArchiveCase(
                "zip",
                "cargo-nextest.zip",
                {"pkg/README": b"docs"},
                "extracted.exe",
                r"cargo-nextest\.exe missing from",
            ),
            id="zip-missing-executable",
        ),
    ],
)
def test_extract_binary_rejects_archive_without_executable(
    case: _RejectArchiveCase,
    tmp_path: Path,
    install_nextest_module: ModuleType,
) -> None:
    """Archives lacking the expected executable raise a clear ``ValueError``."""
    archive = tmp_path / case.filename
    _write_archive(archive, case.members, archive_format=case.archive_format)
    asset = (
        _windows_asset(install_nextest_module)
        if case.archive_format == "zip"
        else _nextest_asset(install_nextest_module, "0" * 64)
    )

    with pytest.raises(ValueError, match=case.expected_match):
        install_nextest_module._extract_binary(
            archive,
            asset,
            tmp_path / case.destination_name,
        )


@pytest.mark.parametrize(
    "failure",
    [None, *_NEXTEST_FAILURE_MODES],
    ids=["none", *_NEXTEST_FAILURE_MODES],
)
def test_install_nextest_install_order_invariants(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    failure: str | None,
) -> None:
    """Installation replaces the destination only when both digests verify.

    The five possible ``failure`` values (``None`` plus each entry in
    ``_NEXTEST_FAILURE_MODES``) are an exhaustive, fixed set, so this is a
    plain parametrization rather than a Hypothesis search: there is no
    larger input space to explore, and repeating whole install attempts --
    each with real filesystem work -- across many random draws added no
    extra coverage over enumerating the five cases once.
    """
    cargo_home = tmp_path / "cargo-home"
    outcome = _attempt_nextest_install(
        install_nextest_module,
        cargo_home,
        failure,
    )

    if failure is None:
        assert not outcome.temporary_exists
        assert outcome.exit_code is None
        assert outcome.destination_bytes == _NEXTEST_BINARY_PAYLOAD
    else:
        _assert_install_failed_preserving_sentinel(outcome)
    assert outcome.extracted is (failure not in {"download", "archive-digest"})


def test_install_cargo_nextest_end_to_end_from_local_archive(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install_cargo_nextest`` downloads, verifies, extracts, and installs.

    A real gzip tar archive built in ``tmp_path`` stands in for the official
    release. The download step is redirected to read that local file, so the
    full digest-verification and atomic-install path runs without any
    network request.
    """
    binary_payload = b"#!/bin/sh\necho cargo-nextest-fixture\n"
    archive = tmp_path / "cargo-nextest-fixture.tar.gz"
    _write_tar_archive(archive, {"cargo-nextest": binary_payload})
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    binary_sha = hashlib.sha256(binary_payload).hexdigest()
    asset = install_nextest_module.ReleaseAsset(
        "x86_64-unknown-linux-gnu",
        "tar.gz",
        archive_sha,
    )

    def fake_urlopen(
        _request: object,
        timeout: float | None = None,
    ) -> typ.IO[bytes]:
        return archive.open("rb")

    monkeypatch.setattr(install_nextest_module.urllib.request, "urlopen", fake_urlopen)
    cargo_home = tmp_path / "cargo-home"
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    destination = install_nextest_module.install_cargo_nextest(asset, binary_sha)

    assert destination == cargo_home / "bin" / "cargo-nextest"
    assert destination.read_bytes() == binary_payload
    assert destination.stat().st_mode & 0o111
    assert not destination.with_suffix(f"{destination.suffix}.tmp").exists()


@dataclasses.dataclass(frozen=True)
class _PinnedRelease:
    """Literal, checked-in expectations for one platform's pinned release.

    These values are copied by hand from ``install_cargo_nextest.py``'s
    ``CARGO_NEXTEST_VERSION``, ``CARGO_NEXTEST_RELEASE_ASSETS``, and
    ``CARGO_NEXTEST_SHA256`` constants -- they are literals, not references
    to those constants -- so that bumping a pin in the script makes this
    test fail until a human deliberately updates this table too. Do not
    "simplify" this back into a lookup against the script's own constants;
    doing so would silently defeat the point of the test.
    """

    version: str
    target: str
    extension: str
    archive_sha256: str
    executable_sha256: str


@dataclasses.dataclass(frozen=True)
class _PinnedPlatformDetection:
    """The ``platform.system``/``platform.machine``/musl combination for a key."""

    system: str
    machine: str
    is_musl: bool


_NEXTEST_PINNED_RELEASES: dict[str, _PinnedRelease] = {
    "linux-x86_64-gnu": _PinnedRelease(
        "0.9.120",
        "x86_64-unknown-linux-gnu",
        "tar.gz",
        "a5b1c12500c47e27af4baf533c917bf1b38e9bf2e6ffb063dfa1de6e75aa8726",
        "8d717594668f0ec817405b9526cb657ca40fc888068277004860d0f253837d14",
    ),
    "linux-x86_64-musl": _PinnedRelease(
        "0.9.120",
        "x86_64-unknown-linux-musl",
        "tar.gz",
        "e00511fc23241ffd3ca1d95b23bde8a9cd0fb96bb691a9957a909ba74e7a5238",
        "b05373ac79d5a1e200627ffd780c9cec96d7547311ac585d6c277d6394c2cd28",
    ),
    "linux-aarch64-gnu": _PinnedRelease(
        "0.9.120",
        "aarch64-unknown-linux-gnu",
        "tar.gz",
        "5e13751733a1fc4d26984ad5e1bce10d057d95299b02ed3ac96877b7288c8feb",
        "901f10642066a848d4bc4eaee3d91642ad0476bea4a5de26832e838e4c32939e",
    ),
    "mac-universal": _PinnedRelease(
        "0.9.120",
        "universal-apple-darwin",
        "tar.gz",
        "e2aa5a27bfdac66c913346985a1ceff50ab9590b846798440464410bd5a309b9",
        "d9f8aa57f88ea948ee68629cfc22a0a86ccd0d0143139983753dcb5f167085b8",
    ),
    "windows-x86_64": _PinnedRelease(
        "0.9.120",
        "x86_64-pc-windows-msvc",
        "zip",
        "ccb22cb26d6816eb39992f276c0f058ea9a5842ee35f70ee48a4ee84fd671538",
        "8e4160a8d710e753fd21a725e1771d20d948dbfa5d3472b57ee331f16c237af4",
    ),
    "windows-aarch64": _PinnedRelease(
        "0.9.120",
        "aarch64-pc-windows-msvc",
        "zip",
        "8b6475c9d6fd6946a8a8cced8213c1e5b1f9df219cab999831905187887003f9",
        "9a1756ef23dff328f25ebf21c10be5dac7907e111782db63519474ec397f665c",
    ),
}

_NEXTEST_PIN_PLATFORM_DETECTION: dict[str, _PinnedPlatformDetection] = {
    "linux-x86_64-gnu": _PinnedPlatformDetection("Linux", "x86_64", is_musl=False),
    "linux-x86_64-musl": _PinnedPlatformDetection("Linux", "x86_64", is_musl=True),
    "linux-aarch64-gnu": _PinnedPlatformDetection("Linux", "aarch64", is_musl=False),
    "mac-universal": _PinnedPlatformDetection("Darwin", "arm64", is_musl=False),
    "windows-x86_64": _PinnedPlatformDetection("Windows", "AMD64", is_musl=False),
    "windows-aarch64": _PinnedPlatformDetection("Windows", "ARM64", is_musl=False),
}


def test_nextest_pin_table_covers_every_supported_platform(
    install_nextest_module: ModuleType,
) -> None:
    """The checked-in pin table must track every platform the script pins.

    This guards against a newly supported platform being added to
    ``CARGO_NEXTEST_RELEASE_ASSETS``/``CARGO_NEXTEST_SHA256`` without a
    matching literal entry in ``_NEXTEST_PINNED_RELEASES`` above.
    """
    assert set(_NEXTEST_PIN_PLATFORM_DETECTION) == set(
        install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS
    )
    assert set(_NEXTEST_PINNED_RELEASES) == set(
        install_nextest_module.CARGO_NEXTEST_SHA256
    )


@pytest.mark.parametrize("key", sorted(_NEXTEST_PINNED_RELEASES))
def test_install_cargo_nextest_pins_expected_release_per_platform(
    key: str,
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` resolves the exact pinned release for every supported platform.

    ``main`` runs through the real ``_release_for_platform`` (only OS,
    architecture, and musl detection are monkeypatched), so a pin change in
    the script -- version, target triple, archive extension, archive
    digest, or executable digest -- makes this test fail until the literal
    ``_NEXTEST_PINNED_RELEASES`` table above is deliberately updated too.
    The download and extraction steps are stubbed so no network request is
    made; the stubbed archive bytes then deliberately fail the script's
    real archive-digest check, so ``main`` always exits with an error after
    the resolved asset has been captured.
    """
    detection = _NEXTEST_PIN_PLATFORM_DETECTION[key]
    expected = _NEXTEST_PINNED_RELEASES[key]
    captured: dict[str, object] = {}

    def fake_download(asset: object, target: Path) -> None:
        captured["asset"] = asset
        target.write_bytes(b"stub-archive")

    def fake_extract(_archive: Path, _asset: object, target: Path) -> None:
        target.write_bytes(b"stub-binary")

    monkeypatch.setattr(
        install_nextest_module.platform, "system", lambda: detection.system
    )
    monkeypatch.setattr(
        install_nextest_module.platform, "machine", lambda: detection.machine
    )
    monkeypatch.setattr(install_nextest_module, "_is_musl", lambda: detection.is_musl)
    # A real cargo-nextest may already be on PATH or in CARGO_HOME in the
    # ambient environment; force the platform-appropriate release resolution
    # to run so this test does not depend on that.
    monkeypatch.setattr(install_nextest_module, "_resolve_nextest_binary", lambda: None)
    monkeypatch.setattr(install_nextest_module, "_download_archive", fake_download)
    monkeypatch.setattr(install_nextest_module, "_extract_binary", fake_extract)
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "cargo-home"))

    with pytest.raises(install_nextest_module.typer.Exit):
        install_nextest_module.main()

    asset = captured["asset"]
    assert expected.version == install_nextest_module.CARGO_NEXTEST_VERSION
    assert asset.target == expected.target
    assert asset.extension == expected.extension
    assert asset.sha256 == expected.archive_sha256
    assert (
        install_nextest_module.CARGO_NEXTEST_SHA256[key] == expected.executable_sha256
    )


def _read_step_summary(path: Path) -> list[str]:
    """Return the lines written to a job summary file, or an empty list."""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_emit_metric_appends_to_step_summary(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``emit_metric`` appends one line per call to the job summary file."""
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    install_nextest_module.emit_metric("cargo-nextest.example=ok")
    install_nextest_module.emit_metric("cargo-nextest.example=again")

    assert summary.read_text(encoding="utf-8") == (
        "cargo-nextest.example=ok\ncargo-nextest.example=again\n"
    )


def test_emit_metric_does_nothing_without_step_summary(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``emit_metric`` is a no-op when ``GITHUB_STEP_SUMMARY`` is unset."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    summary = tmp_path / "step-summary.md"

    install_nextest_module.emit_metric("cargo-nextest.example=ok")

    assert not summary.exists()


def test_install_cargo_nextest_metrics_for_successful_install(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful install emits one bounded metric line per verified step.

    Drives the real ``_download_archive`` (redirected to a local fixture
    archive, as in ``test_install_cargo_nextest_end_to_end_from_local_archive``)
    rather than the stubbed helper used elsewhere in this module, so the
    download metric -- the one metric that helper never exercises -- is
    covered too.
    """
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    binary_payload = b"#!/bin/sh\necho cargo-nextest-fixture\n"
    archive = tmp_path / "cargo-nextest-fixture.tar.gz"
    _write_tar_archive(archive, {"cargo-nextest": binary_payload})
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    binary_sha = hashlib.sha256(binary_payload).hexdigest()
    asset = install_nextest_module.ReleaseAsset(
        "x86_64-unknown-linux-gnu", "tar.gz", archive_sha
    )

    def fake_urlopen(_request: object, timeout: float | None = None) -> typ.IO[bytes]:
        return archive.open("rb")

    monkeypatch.setattr(install_nextest_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "cargo-home"))

    install_nextest_module.install_cargo_nextest(asset, binary_sha)

    lines = _read_step_summary(summary)
    assert len(lines) == 4
    assert lines[0].startswith("cargo-nextest.download=ok duration_seconds=")
    assert lines[0].endswith(f"bytes={archive.stat().st_size}")
    assert lines[1] == "cargo-nextest.archive-digest=ok"
    assert lines[2] == "cargo-nextest.binary-digest=ok"
    assert lines[3] == "cargo-nextest.install=ok"


def test_install_cargo_nextest_metrics_for_binary_digest_mismatch(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binary-digest mismatch emits the failing step and the aggregate outcome.

    Uses the stubbed ``_attempt_nextest_install`` helper, whose ``download``
    stub replaces ``_download_archive`` outright, so no download metric is
    expected here; the real download path is covered separately above.
    """
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    outcome = _attempt_nextest_install(
        install_nextest_module,
        tmp_path / "cargo-home",
        "binary-digest",
    )

    assert outcome.exit_code == 1
    assert _read_step_summary(summary) == [
        "cargo-nextest.archive-digest=ok",
        "cargo-nextest.binary-digest=mismatch",
        "cargo-nextest.install=failed",
    ]


def test_main_metrics_for_reused_binary(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing an already-verified binary emits its own bounded metrics."""
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    binary = tmp_path / "cargo-nextest"
    payload = b"already-verified"
    binary.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    asset = install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS["linux-x86_64-gnu"]
    monkeypatch.setattr(
        install_nextest_module, "_release_for_platform", lambda: (expected, asset)
    )
    monkeypatch.setattr(
        install_nextest_module, "_resolve_nextest_binary", lambda: binary
    )

    install_nextest_module.main()

    assert _read_step_summary(summary) == [
        "cargo-nextest.binary-digest=ok",
        "cargo-nextest.install=reused",
    ]
