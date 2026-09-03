"""Verify the verified cargo-nextest binary wins over one already on PATH.

Installing a verified replacement is not enough on its own: an unverified
``cargo-nextest`` earlier on ``PATH`` would still be the binary later coverage
steps resolve, which is the very binary that failed verification. These tests
drive ``main`` with a shadowing binary in place and assert the installed one
resolves afterwards, and that the run fails loudly when it cannot.

This module is collected on every platform the CI matrix runs, including
Windows, so every fixture below selects its archive extension and expected
executable name from the host platform rather than hard-coding a Linux
``tar.gz`` asset. This mirrors the target/extension lookup
``install_cargo_nextest`` itself performs in ``_release_for_platform``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_cargo_nextest.py"
_VERIFIED_PAYLOAD = b"verified-cargo-nextest"
_SHADOWING_PAYLOAD = b"shadowing-cargo-nextest"


@pytest.fixture
def nextest_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``install_cargo_nextest`` module.

    Clears ``GITHUB_STEP_SUMMARY`` and ``GITHUB_PATH`` so these tests
    cannot leak bounded metric lines or PATH exports into the real job when
    this suite itself runs inside a GitHub Actions job. Tests that exercise
    ``GITHUB_PATH`` explicitly point it at a ``tmp_path`` file below.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.delitem(sys.modules, "install_cargo_nextest", raising=False)
    spec = importlib.util.spec_from_file_location("install_cargo_nextest", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - import failure.
        message = f"could not load {_SCRIPT}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _host_release_asset(module: ModuleType) -> object:
    """Return the real pinned release asset for the host running the tests."""
    key = module._platform_key()
    return module.CARGO_NEXTEST_RELEASE_ASSETS[key]


def _host_executable_name(module: ModuleType) -> str:
    """Return the expected ``cargo-nextest`` filename for the host platform.

    Mirrors the suffix decision ``install_cargo_nextest`` makes from the
    resolved asset's extension (``.exe`` for the ``zip`` Windows archives,
    no suffix for the ``tar.gz`` Linux/macOS archives).
    """
    asset = _host_release_asset(module)
    suffix = ".exe" if asset.extension == "zip" else ""
    return f"cargo-nextest{suffix}"


def _write_executable(path: Path, payload: bytes) -> None:
    """Write an executable stub binary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o755)


def _install_stubs(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed_payload: bytes,
) -> None:
    """Stub the download and extraction so no network is used."""
    archive_payload = b"archive"
    host_asset = _host_release_asset(module)

    def fake_download(_asset: object, destination: Path) -> None:
        destination.write_bytes(archive_payload)

    def fake_extract(_archive: Path, _asset: object, destination: Path) -> None:
        destination.write_bytes(installed_payload)

    monkeypatch.setattr(module, "_download_archive", fake_download)
    monkeypatch.setattr(module, "_extract_binary", fake_extract)
    monkeypatch.setattr(
        module,
        "_release_for_platform",
        lambda: (
            hashlib.sha256(_VERIFIED_PAYLOAD).hexdigest(),
            module.ReleaseAsset(
                host_asset.target,
                host_asset.extension,
                hashlib.sha256(archive_payload).hexdigest(),
            ),
        ),
    )


@pytest.fixture
def shadowed_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nextest_module: ModuleType,
) -> tuple[Path, Path]:
    """Place an unverified cargo-nextest ahead of an empty Cargo bin on PATH."""
    executable_name = _host_executable_name(nextest_module)
    shadow_dir = tmp_path / "usr-local-bin"
    _write_executable(shadow_dir / executable_name, _SHADOWING_PAYLOAD)
    cargo_home = tmp_path / "cargo-home"
    (cargo_home / "bin").mkdir(parents=True)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    monkeypatch.setenv("PATH", str(shadow_dir))
    monkeypatch.setenv("GITHUB_PATH", str(tmp_path / "github-path"))
    return shadow_dir, cargo_home


def test_installed_binary_takes_precedence_over_a_shadowing_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nextest_module: ModuleType,
    shadowed_environment: tuple[Path, Path],
) -> None:
    """The Cargo bin directory is prepended so the verified binary resolves."""
    _, cargo_home = shadowed_environment
    _install_stubs(nextest_module, monkeypatch, installed_payload=_VERIFIED_PAYLOAD)

    nextest_module.main()

    destination = cargo_home / "bin" / _host_executable_name(nextest_module)
    assert destination.read_bytes() == _VERIFIED_PAYLOAD
    assert nextest_module._resolve_nextest_binary() == destination
    assert os.environ["PATH"].split(os.pathsep)[0] == str(cargo_home / "bin")
    exported = (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()
    assert str(cargo_home / "bin") in exported


def test_reports_a_shadowed_installation_instead_of_continuing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    nextest_module: ModuleType,
    shadowed_environment: tuple[Path, Path],
) -> None:
    """A binary that still shadows the installed one is a hard failure."""
    shadow_dir, cargo_home = shadowed_environment
    _install_stubs(nextest_module, monkeypatch, installed_payload=_VERIFIED_PAYLOAD)
    executable_name = _host_executable_name(nextest_module)
    monkeypatch.setattr(
        nextest_module,
        "_resolve_nextest_binary",
        lambda: shadow_dir / executable_name,
    )

    with pytest.raises(nextest_module.typer.Exit) as excinfo:
        nextest_module.main()

    assert excinfo.value.exit_code == 1
    assert "not the verified binary installed at" in capsys.readouterr().err
    assert (cargo_home / "bin" / executable_name).read_bytes() == _VERIFIED_PAYLOAD


def test_reused_binary_in_cargo_home_exports_its_directory_to_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nextest_module: ModuleType,
) -> None:
    """A verified binary found only via ``CARGO_HOME/bin`` still exports PATH.

    ``_resolve_nextest_binary`` falls back to ``CARGO_HOME/bin`` even when
    that directory is absent from ``PATH``. Reusing the binary found there
    must still prepend its directory, or later steps in the same job cannot
    resolve ``cargo-nextest`` even though installation was skipped as
    already satisfied.
    """
    executable_name = _host_executable_name(nextest_module)
    cargo_home = tmp_path / "cargo-home"
    cargo_bin = cargo_home / "bin"
    _write_executable(cargo_bin / executable_name, _VERIFIED_PAYLOAD)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    # PATH deliberately excludes cargo_bin, so only the CARGO_HOME/bin
    # fallback in ``_resolve_nextest_binary`` can find the binary.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setenv("GITHUB_PATH", str(tmp_path / "github-path"))
    monkeypatch.setattr(
        nextest_module,
        "_release_for_platform",
        lambda: (
            hashlib.sha256(_VERIFIED_PAYLOAD).hexdigest(),
            _host_release_asset(nextest_module),
        ),
    )

    def fail_install(*_args: object) -> None:
        raise AssertionError

    monkeypatch.setattr(nextest_module, "install_cargo_nextest", fail_install)

    nextest_module.main()

    assert os.environ["PATH"].split(os.pathsep)[0] == str(cargo_bin)
    exported = (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()
    assert str(cargo_bin) in exported
