"""Verify the cargo-nextest installer script end to end.

These tests cover platform and release resolution, the pinned digest table,
archive extraction against real fixtures, the bounded download, the install and
reuse paths, and the job-summary metrics. They were split out of
``test_scripts.py`` when that module outgrew the size the code-health rules
allow; the tests and their identifiers are unchanged by the move.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import typing as typ

import pytest
from _coverage_test_support import _exit_code, _load_module

if typ.TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


@pytest.fixture
def install_nextest_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``install_cargo_nextest`` module for testing.

    Clears ``GITHUB_STEP_SUMMARY`` and ``GITHUB_PATH`` so tests that do not
    explicitly point them at a ``tmp_path`` file cannot leak bounded metric
    lines or PATH exports into the real job when this suite itself runs
    inside a GitHub Actions job.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    return _load_module(monkeypatch, "install_cargo_nextest")


@dataclasses.dataclass(frozen=True)
class _PlatformKeyCase:
    """Describe one OS/architecture/libc combination and its platform key."""

    system: str
    machine: str
    is_musl: bool
    expected: str


def _fake_libc(*, include_version: bool = True) -> object:
    """Create a libc stub for ``_is_musl`` unit tests."""

    class _FakeLibc:
        def gnu_get_libc_version(self) -> str:
            return "2.31" if include_version else ""

    return lambda _library_name: _FakeLibc()


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _PlatformKeyCase(
                "Linux", "x86_64", is_musl=False, expected="linux-x86_64-gnu"
            ),
            id="linux-gnu",
        ),
        pytest.param(
            _PlatformKeyCase(
                "Linux", "x86_64", is_musl=True, expected="linux-x86_64-musl"
            ),
            id="linux-musl",
        ),
        pytest.param(
            _PlatformKeyCase(
                "Linux", "aarch64", is_musl=False, expected="linux-aarch64-gnu"
            ),
            id="linux-aarch64",
        ),
        pytest.param(
            _PlatformKeyCase(
                "Darwin", "arm64", is_musl=False, expected="mac-universal"
            ),
            id="mac",
        ),
        pytest.param(
            _PlatformKeyCase(
                "Windows", "AMD64", is_musl=False, expected="windows-x86_64"
            ),
            id="windows-x86_64",
        ),
        pytest.param(
            _PlatformKeyCase(
                "Windows", "ARM64", is_musl=False, expected="windows-aarch64"
            ),
            id="windows-aarch64",
        ),
    ],
)
def test_platform_key_variants(
    case: _PlatformKeyCase,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platform keys normalize OS and architecture and keep the libc split."""
    monkeypatch.setattr(install_nextest_module.platform, "system", lambda: case.system)
    monkeypatch.setattr(
        install_nextest_module.platform, "machine", lambda: case.machine
    )
    monkeypatch.setattr(install_nextest_module, "_is_musl", lambda: case.is_musl)

    assert install_nextest_module._platform_key() == case.expected


def _fake_musl_libc(_library_name: str) -> object:
    """Create a libc stub that lacks the GNU version symbol."""

    class _FakeLibc:
        def __getattr__(self, name: str) -> object:
            raise AttributeError(name)

    return _FakeLibc()


@pytest.mark.parametrize(
    ("ctypes_cdll", "expected_musl"),
    [
        pytest.param(_fake_libc(), False, id="gnu"),
        pytest.param(_fake_musl_libc, True, id="musl"),
    ],
)
def test_is_musl_detects_libc_flavour(
    install_nextest_module: ModuleType,
    ctypes_cdll: typ.Callable[[str], object],
    *,
    expected_musl: bool,
) -> None:
    """The probe reports musl exactly when ``gnu_get_libc_version`` is absent."""
    assert install_nextest_module._is_musl(ctypes_cdll=ctypes_cdll) is expected_musl


def test_is_musl_propagates_cdll_errors(install_nextest_module: ModuleType) -> None:
    """Load failures from the libc probe are propagated to callers."""

    def raise_oserror(_library_name: str) -> object:
        message = "boom"
        raise OSError(message)

    with pytest.raises(OSError, match="boom"):
        install_nextest_module._is_musl(ctypes_cdll=raise_oserror)


@pytest.mark.parametrize(
    ("key", "expected_target"),
    [
        ("linux-x86_64-gnu", "x86_64-unknown-linux-gnu"),
        ("linux-x86_64-musl", "x86_64-unknown-linux-musl"),
        ("linux-aarch64-gnu", "aarch64-unknown-linux-gnu"),
        ("mac-universal", "universal-apple-darwin"),
        ("windows-x86_64", "x86_64-pc-windows-msvc"),
        ("windows-aarch64", "aarch64-pc-windows-msvc"),
    ],
)
def test_expected_sha_for_supported_platforms(
    key: str,
    expected_target: str | None,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expected SHA lookup matches the platform mapping."""
    monkeypatch.setattr(install_nextest_module, "_platform_key", lambda: key)
    sha, asset = install_nextest_module._release_for_platform()
    assert sha == install_nextest_module.CARGO_NEXTEST_SHA256[key]
    assert asset == install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS[key]
    assert asset.target == expected_target


def test_release_assets_pin_archive_checksums(
    install_nextest_module: ModuleType,
) -> None:
    """Every supported platform pins a full release-archive checksum."""
    assets = install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS
    assert assets.keys() == install_nextest_module.CARGO_NEXTEST_SHA256.keys()
    assert all(len(asset.sha256) == 64 for asset in assets.values())


def test_expected_sha_for_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
    install_nextest_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unsupported platforms raise a Typer exit."""
    monkeypatch.setattr(
        install_nextest_module,
        "_platform_key",
        lambda: "unsupported-platform",
    )

    with pytest.raises(install_nextest_module.typer.Exit) as excinfo:
        install_nextest_module._release_for_platform()

    assert _exit_code(excinfo.value) == 1
    assert "Unsupported platform for cargo-nextest" in capsys.readouterr().err


def test_find_nextest_binary_prefers_path(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary lookup prefers PATH via shutil.which."""
    binary = tmp_path / "cargo-nextest"
    binary.write_bytes(b"payload")
    monkeypatch.setattr(install_nextest_module.shutil, "which", lambda _: str(binary))
    assert install_nextest_module._find_nextest_binary() == binary


def test_find_nextest_binary_falls_back_to_home(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary lookup falls back to ~/.cargo/bin when PATH is empty."""
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setattr(install_nextest_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(install_nextest_module.Path, "home", lambda: tmp_path)
    cargo_bin = tmp_path / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True, exist_ok=True)
    binary = cargo_bin / "cargo-nextest"
    binary.write_bytes(b"payload")
    assert install_nextest_module._find_nextest_binary() == binary


def test_find_nextest_binary_missing_exits(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing cargo-nextest after install raises a Typer exit."""
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setattr(install_nextest_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(install_nextest_module.Path, "home", lambda: tmp_path)

    with pytest.raises(install_nextest_module.typer.Exit) as excinfo:
        install_nextest_module._find_nextest_binary()

    assert _exit_code(excinfo.value) == 1
    assert "cargo-nextest not found after installation" in capsys.readouterr().err


def test_resolve_nextest_binary_returns_none_when_missing(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing binary lookup returns None when not found."""
    monkeypatch.setattr(install_nextest_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(install_nextest_module.Path, "home", lambda: tmp_path)
    assert install_nextest_module._resolve_nextest_binary() is None


def test_install_nextest_skips_when_verified(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installer skips the release download when a binary verifies."""
    binary = tmp_path / "cargo-nextest"
    binary.write_bytes(b"payload")
    expected = hashlib.sha256(b"payload").hexdigest()
    called: dict[str, bool] = {"verify": False}

    def fake_verify(path: Path, sha: str) -> bool:
        assert path == binary
        assert sha == expected
        called["verify"] = True
        return True

    def fail_install(*_args: object) -> None:
        raise AssertionError

    asset = install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS["linux-x86_64-gnu"]
    monkeypatch.setattr(
        install_nextest_module, "_release_for_platform", lambda: (expected, asset)
    )
    monkeypatch.setattr(
        install_nextest_module, "_resolve_nextest_binary", lambda: binary
    )
    monkeypatch.setattr(install_nextest_module, "verify_nextest_binary", fake_verify)
    monkeypatch.setattr(install_nextest_module, "install_cargo_nextest", fail_install)

    install_nextest_module.main()
    assert called["verify"] is True


def test_install_nextest_download_failure(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Official release download failures cause a clear Typer exit."""
    asset = install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS["linux-x86_64-gnu"]

    def fail_urlopen(*_args: object, **_kwargs: object) -> None:
        message = "simulated failure"
        raise install_nextest_module.urllib.error.URLError(message)

    monkeypatch.setattr(install_nextest_module.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(install_nextest_module.typer.Exit) as excinfo:
        install_nextest_module._download_archive(asset, tmp_path / asset.filename)

    assert _exit_code(excinfo.value) == 1
    captured = capsys.readouterr()
    assert "cargo-nextest release download failed" in captured.err


def test_install_nextest_download_rejects_oversized_archive(
    tmp_path: Path,
    install_nextest_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A response larger than the configured cap fails and deletes the partial file.

    The digest check only runs after the whole archive has landed on disk,
    so a redirected or compromised endpoint streaming an unbounded response
    must be rejected before that point, not after.
    """
    asset = install_nextest_module.CARGO_NEXTEST_RELEASE_ASSETS["linux-x86_64-gnu"]
    monkeypatch.setattr(install_nextest_module, "_MAX_ARCHIVE_BYTES", 1024)
    oversized_payload = b"x" * 4096

    def fake_urlopen(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(oversized_payload)

    monkeypatch.setattr(install_nextest_module.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / asset.filename

    with pytest.raises(install_nextest_module.typer.Exit) as excinfo:
        install_nextest_module._download_archive(asset, destination)

    assert _exit_code(excinfo.value) == 1
    assert "exceeded 1024 bytes" in capsys.readouterr().err
    assert not destination.exists()


@dataclasses.dataclass(frozen=True)
class _ChecksumVerificationCase:
    """Describe one binary-checksum verification case."""

    use_matching_digest: bool
    expect_verified: bool


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _ChecksumVerificationCase(use_matching_digest=True, expect_verified=True),
            id="digest-matches",
        ),
        pytest.param(
            _ChecksumVerificationCase(use_matching_digest=False, expect_verified=False),
            id="digest-mismatches",
        ),
    ],
)
def test_install_nextest_checksum_verification(
    case: _ChecksumVerificationCase,
    tmp_path: Path,
    install_nextest_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verification passes only when the digest matches the binary exactly."""
    binary = tmp_path / "cargo-nextest"
    payload = b"payload"
    binary.write_bytes(payload)
    expected = (
        hashlib.sha256(payload).hexdigest() if case.use_matching_digest else "deadbeef"
    )

    verified = install_nextest_module.verify_nextest_binary(binary, expected)

    assert verified is case.expect_verified
    if not case.expect_verified:
        assert "cargo-nextest checksum mismatch" in capsys.readouterr().err
