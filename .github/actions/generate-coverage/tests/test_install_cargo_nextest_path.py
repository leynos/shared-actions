"""Verify the verified cargo-nextest binary wins over one already on PATH.

Installing a verified replacement is not enough on its own: an unverified
``cargo-nextest`` earlier on ``PATH`` would still be the binary later coverage
steps resolve, which is the very binary that failed verification. These tests
drive ``main`` with a shadowing binary in place and assert the installed one
resolves afterwards, and that the run fails loudly when it cannot.
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
    """Return a freshly loaded ``install_cargo_nextest`` module."""
    monkeypatch.delitem(sys.modules, "install_cargo_nextest", raising=False)
    spec = importlib.util.spec_from_file_location("install_cargo_nextest", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - import failure.
        message = f"could not load {_SCRIPT}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                "x86_64-unknown-linux-gnu",
                "tar.gz",
                hashlib.sha256(archive_payload).hexdigest(),
            ),
        ),
    )


@pytest.fixture
def shadowed_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Place an unverified cargo-nextest ahead of an empty Cargo bin on PATH."""
    shadow_dir = tmp_path / "usr-local-bin"
    _write_executable(shadow_dir / "cargo-nextest", _SHADOWING_PAYLOAD)
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

    destination = cargo_home / "bin" / "cargo-nextest"
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
    monkeypatch.setattr(
        nextest_module,
        "_resolve_nextest_binary",
        lambda: shadow_dir / "cargo-nextest",
    )

    with pytest.raises(nextest_module.typer.Exit) as excinfo:
        nextest_module.main()

    assert excinfo.value.exit_code == 1
    assert "not the verified binary installed at" in capsys.readouterr().err
    assert (cargo_home / "bin" / "cargo-nextest").read_bytes() == _VERIFIED_PAYLOAD
