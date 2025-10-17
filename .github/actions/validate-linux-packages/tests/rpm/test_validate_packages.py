"""Tests covering RPM package validation behaviour."""

from __future__ import annotations

import contextlib
import typing as typ

import pytest

from tests.validate_linux_packages import (
    RpmPackageParams,
    build_rpm_metadata,
    make_dummy_sandbox,
    write_package,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path
    from types import ModuleType


def test_validate_rpm_package_rejects_unexpected_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    validate_packages_module: ModuleType,
) -> None:
    """RPM validation fails when the release does not match the expected prefix."""
    package = write_package(tmp_path, "rust-toy-app-1.2.3-1.x86_64.rpm")
    metadata = build_rpm_metadata(
        validate_packages_module,
        RpmPackageParams(release="2.el9"),
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_rpm_package",
        lambda *_: metadata,
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected rpm release",
    ):
        validate_packages_module.validate_rpm_package(
            rpm_cmd=object(),
            package_path=package,
            expected_name="rust-toy-app",
            expected_version="1.2.3",
            expected_release="1",
            expected_arch="x86_64",
            expected_paths=("/usr/bin/rust-toy-app",),
            executable_paths=("/usr/bin/rust-toy-app",),
            verify_command=(),
            sandbox_factory=lambda: contextlib.nullcontext(None),
        )


@pytest.mark.parametrize(
    ("arch", "expected"),
    [
        ("amd64", {"amd64", "x86_64"}),
        ("arm64", {"arm64", "aarch64"}),
        ("riscv64", {"riscv64"}),
    ],
)
def test_acceptable_rpm_architectures_cover_aliases(
    arch: str,
    expected: set[str],
    validate_packages_module: ModuleType,
) -> None:
    """acceptable_rpm_architectures returns the canonical alias set."""
    assert validate_packages_module.acceptable_rpm_architectures(arch) == expected


def test_validate_rpm_package_skips_cross_architecture_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    validate_packages_module: ModuleType,
) -> None:
    """Skip RPM sandbox validation when package and host architectures differ."""
    package = write_package(tmp_path, "rust-toy-app-1.2.3-1.aarch64.rpm")
    metadata = build_rpm_metadata(
        validate_packages_module,
        RpmPackageParams(architecture="aarch64"),
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_rpm_package",
        lambda *_: metadata,
    )
    monkeypatch.setattr(
        validate_packages_module.platform,
        "machine",
        lambda: "x86_64",
    )
    caplog.set_level("INFO")

    calls: list[tuple[tuple[str, ...], int | None]] = []

    validate_packages_module.validate_rpm_package(
        rpm_cmd=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_release="1",
        expected_arch="aarch64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=(),
        sandbox_factory=lambda: contextlib.nullcontext(
            make_dummy_sandbox(tmp_path, calls)
        ),
    )

    assert not calls
    assert (tmp_path / "sandbox" / package.name).exists() is False
    assert "skipping rpm package sandbox validation" in caplog.text


def test_validate_rpm_package_skips_using_metadata_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    validate_packages_module: ModuleType,
) -> None:
    """Skip RPM sandbox using the architecture from package metadata."""
    package = write_package(tmp_path, "rust-toy-app-1.2.3-1.aarch64.rpm")
    metadata = build_rpm_metadata(
        validate_packages_module,
        RpmPackageParams(architecture="aarch64"),
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_rpm_package",
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

    validate_packages_module.validate_rpm_package(
        rpm_cmd=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_release="1",
        expected_arch="x86_64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=(),
        sandbox_factory=_fail_sandbox,
    )

    assert "skipping rpm package sandbox validation" in caplog.text


def test_validate_rpm_package_rejects_unexpected_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    """Raise an error when RPM metadata architecture conflicts with expectation."""
    package = write_package(tmp_path, "rust-toy-app-1.2.3-1.x86_64.rpm")
    metadata = build_rpm_metadata(validate_packages_module)
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_rpm_package",
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
        match="unexpected rpm architecture",
    ):
        validate_packages_module.validate_rpm_package(
            rpm_cmd=object(),
            package_path=package,
            expected_name="rust-toy-app",
            expected_version="1.2.3",
            expected_release="1",
            expected_arch="arm64",
            expected_paths=("/usr/bin/rust-toy-app",),
            executable_paths=("/usr/bin/rust-toy-app",),
            verify_command=(),
            sandbox_factory=_fail_sandbox,
        )
