"""Tests covering Debian package validation behaviour."""

from __future__ import annotations

import contextlib
import typing as typ

import pytest

from tests.helpers import build_deb_metadata, make_dummy_sandbox, write_package

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
    from types import ModuleType


def test_validate_deb_package_runs_sandbox_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    """Debian validation installs the package and exercises all checks."""
    package = write_package(tmp_path, "rust-toy-app_1.2.3-1_amd64.deb")
    metadata = build_deb_metadata(
        validate_packages_module,
        files={"/usr/bin/rust-toy-app", "/usr/share/doc/rust-toy-app"},
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )
    calls: list[tuple[tuple[str, ...], int | None]] = []
    sandbox = make_dummy_sandbox(tmp_path, calls)

    validate_packages_module.validate_deb_package(
        dpkg_deb=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_deb_version="1.2.3-1",
        expected_arch="amd64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=("/usr/bin/rust-toy-app", "--version"),
        sandbox_factory=lambda: contextlib.nullcontext(sandbox),
    )

    assert (tmp_path / "sandbox" / package.name).exists()
    recorded = {args for args, _ in calls}
    assert (
        "dpkg",
        "-i",
        f"/{package.name}",
    ) in recorded
    assert ("test", "-e", "/usr/bin/rust-toy-app") in recorded
    assert ("test", "-x", "/usr/bin/rust-toy-app") in recorded
    assert ("/usr/bin/rust-toy-app", "--version") in recorded
    assert ("dpkg", "-r", "rust-toy-app") in recorded


def test_validate_deb_package_skips_cross_architecture_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    validate_packages_module: ModuleType,
) -> None:
    """Skip Debian sandbox validation when package and host architectures differ."""
    package = write_package(tmp_path, "rust-toy-app_1.2.3-1_arm64.deb")
    metadata = build_deb_metadata(
        validate_packages_module,
        architecture="arm64",
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )
    caplog.set_level("INFO")

    calls: list[tuple[tuple[str, ...], int | None]] = []

    validate_packages_module.validate_deb_package(
        dpkg_deb=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_deb_version="1.2.3-1",
        expected_arch="arm64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=("/usr/bin/rust-toy-app", "--version"),
        sandbox_factory=lambda: contextlib.nullcontext(
            make_dummy_sandbox(tmp_path, calls)
        ),
    )

    assert not calls
    assert (tmp_path / "sandbox" / package.name).exists() is False
    assert "skipping deb package sandbox validation" in caplog.text


def test_validate_deb_package_skips_using_metadata_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    validate_packages_module: ModuleType,
) -> None:
    """Skip sandbox when metadata architecture differs from expected config."""
    package = write_package(tmp_path, "rust-toy-app_1.2.3-1_arm64.deb")
    metadata = build_deb_metadata(
        validate_packages_module,
        architecture="arm64",
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )
    caplog.set_level("INFO")

    def _fail_sandbox() -> typ.ContextManager[object]:
        message = "sandbox used"
        raise AssertionError(message)

    validate_packages_module.validate_deb_package(
        dpkg_deb=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_deb_version="1.2.3-1",
        expected_arch="amd64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=("/usr/bin/rust-toy-app", "--version"),
        sandbox_factory=_fail_sandbox,
    )

    assert "skipping deb package sandbox validation" in caplog.text


def test_validate_deb_package_rejects_unexpected_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    """Raise an error when metadata architecture conflicts with expectation."""
    package = write_package(tmp_path, "rust-toy-app_1.2.3-1_amd64.deb")
    metadata = build_deb_metadata(validate_packages_module)
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )

    def _fail_sandbox() -> typ.ContextManager[object]:
        message = "sandbox used"
        raise AssertionError(message)

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected deb architecture",
    ):
        validate_packages_module.validate_deb_package(
            dpkg_deb=object(),
            package_path=package,
            expected_name="rust-toy-app",
            expected_version="1.2.3",
            expected_deb_version="1.2.3-1",
            expected_arch="arm64",
            expected_paths=("/usr/bin/rust-toy-app",),
            executable_paths=("/usr/bin/rust-toy-app",),
            verify_command=("/usr/bin/rust-toy-app", "--version"),
            sandbox_factory=_fail_sandbox,
        )
