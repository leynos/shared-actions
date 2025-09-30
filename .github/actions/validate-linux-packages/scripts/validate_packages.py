"""Package validation helpers for the validate-linux-packages action."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Callable, Collection, ContextManager, Iterable, Tuple

from plumbum.commands.base import BaseCommand
from plumbum.commands.processes import ProcessExecutionError

from script_utils import ensure_directory, unique_match

from validate_exceptions import ValidationError
from validate_metadata import inspect_deb_package, inspect_rpm_package
from validate_polythene import PolytheneSession

__all__ = [
    "acceptable_rpm_architectures",
    "ensure_subset",
    "locate_deb",
    "locate_rpm",
    "validate_deb_package",
    "validate_rpm_package",
]

SandboxFactory = Callable[[], ContextManager[PolytheneSession]]


def acceptable_rpm_architectures(arch: str) -> set[str]:
    """Return accepted RPM architecture aliases for nfpm ``arch``."""

    aliases = {
        "amd64": {"amd64", "x86_64"},
        "386": {"386", "i386", "i486", "i586", "i686"},
        "arm": {"arm", "armhfp", "armv7hl"},
        "arm64": {"arm64", "aarch64"},
        "riscv64": {"riscv64"},
        "ppc64le": {"ppc64le"},
        "s390x": {"s390x"},
        "loong64": {"loong64", "loongarch64"},
    }
    return aliases.get(arch, {arch})


def locate_deb(package_dir: Path, package_name: str, version: str, release: str) -> Path:
    """Return the Debian package matching ``package_name`` and ``version``."""

    pattern = f"{package_name}_{version}-{release}_*.deb"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} deb package"
    )


def locate_rpm(package_dir: Path, package_name: str, version: str, release: str) -> Path:
    """Return the RPM package matching ``package_name`` and ``version``."""

    pattern = f"{package_name}-{version}-{release}*.rpm"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} rpm package"
    )


def ensure_subset(expected: Collection[str], actual: Collection[str], label: str) -> None:
    """Raise :class:`ValidationError` when ``expected`` is not contained in ``actual``."""

    missing = [path for path in expected if path not in actual]
    if missing:
        raise ValidationError(f"missing {label}: {', '.join(missing)}")


def _install_and_verify(
    sandbox_factory: SandboxFactory,
    package_path: Path,
    expected_paths: Iterable[str],
    executable_paths: Iterable[str],
    verify_command: Tuple[str, ...],
    install_command: Tuple[str, ...],
    remove_command: Tuple[str, ...] | None,
    *,
    install_error: str,
) -> None:
    with sandbox_factory() as sandbox:
        dest = sandbox.root / package_path.name
        ensure_directory(dest.parent)
        dest.write_bytes(package_path.read_bytes())
        try:
            sandbox.exec(*install_command)
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
            raise ValidationError(f"{install_error}: {exc}") from exc
        for path in expected_paths:
            sandbox.exec("test", "-e", path)
        for path in executable_paths:
            sandbox.exec("test", "-x", path)
        if verify_command:
            sandbox.exec(*verify_command)
        if remove_command is not None:
            with contextlib.suppress(ProcessExecutionError):
                sandbox.exec(*remove_command)


def validate_deb_package(
    dpkg_deb: BaseCommand,
    package_path: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_deb_version: str,
    expected_arch: str,
    expected_paths: Collection[str],
    executable_paths: Collection[str],
    verify_command: Tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate Debian package metadata and sandbox installation."""

    metadata = inspect_deb_package(dpkg_deb, package_path)
    if metadata.name != expected_name:
        raise ValidationError(
            f"unexpected package name: expected {expected_name!r}, found {metadata.name!r}"
        )
    if metadata.version not in {expected_deb_version, expected_version}:
        raise ValidationError(
            f"unexpected deb version: expected {expected_deb_version!r}, found {metadata.version!r}"
        )
    if metadata.architecture != expected_arch:
        raise ValidationError(
            f"unexpected deb architecture: expected {expected_arch!r}, found {metadata.architecture!r}"
        )
    ensure_subset(expected_paths, metadata.files, "Debian package payload")

    _install_and_verify(
        sandbox_factory,
        package_path,
        expected_paths,
        executable_paths,
        verify_command,
        install_command=("dpkg", "-i", package_path.name),
        remove_command=("dpkg", "-r", expected_name),
        install_error="dpkg installation failed",
    )


def validate_rpm_package(
    rpm_cmd: BaseCommand,
    package_path: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_release: str,
    expected_arch: str,
    expected_paths: Collection[str],
    executable_paths: Collection[str],
    verify_command: Tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate RPM package metadata and sandbox installation."""

    metadata = inspect_rpm_package(rpm_cmd, package_path)
    if metadata.name != expected_name:
        raise ValidationError(
            f"unexpected package name: expected {expected_name!r}, found {metadata.name!r}"
        )
    if metadata.version != expected_version:
        raise ValidationError(
            f"unexpected rpm version: expected {expected_version!r}, found {metadata.version!r}"
        )
    if metadata.release and not metadata.release.startswith(expected_release):
        raise ValidationError(
            f"unexpected rpm release: expected prefix {expected_release!r}, found {metadata.release!r}"
        )
    acceptable_arches = acceptable_rpm_architectures(expected_arch)
    if metadata.architecture not in acceptable_arches:
        raise ValidationError(
            "unexpected rpm architecture: expected one of "
            f"{sorted(acceptable_arches)!r}, found {metadata.architecture!r}"
        )
    ensure_subset(expected_paths, metadata.files, "RPM package payload")

    _install_and_verify(
        sandbox_factory,
        package_path,
        expected_paths,
        executable_paths,
        verify_command,
        install_command=(
            "rpm",
            "-i",
            "--nodeps",
            "--nosignature",
            package_path.name,
        ),
        remove_command=("rpm", "-e", expected_name),
        install_error="rpm installation failed",
    )
