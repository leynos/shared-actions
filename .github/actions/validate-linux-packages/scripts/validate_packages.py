"""Package validation helpers for the validate-linux-packages action."""

from __future__ import annotations

import logging
import pathlib
import typing as typ

from plumbum.commands.processes import ProcessExecutionError
from validate_exceptions import ValidationError
from validate_helpers import ensure_directory, unique_match
from validate_metadata import (
    DebMetadata,
    RpmMetadata,
    inspect_deb_package,
    inspect_rpm_package,
)
from validate_polythene import PolytheneSession

logger = logging.getLogger(__name__)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime fallbacks
    Path = pathlib.Path
    BaseCommand = object

__all__ = [
    "acceptable_rpm_architectures",
    "ensure_subset",
    "locate_deb",
    "locate_rpm",
    "rpm_expected_architecture",
    "validate_deb_package",
    "validate_rpm_package",
]

SandboxFactory = typ.Callable[[], typ.ContextManager[PolytheneSession]]
MetaT = typ.TypeVar("MetaT", bound="_SupportsFiles")


class _SupportsFiles(typ.Protocol):
    files: typ.Collection[str]


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


def rpm_expected_architecture(arch: str) -> str:
    """Return canonical RPM architecture for nfpm ``arch`` values."""
    return "x86_64" if arch == "amd64" else arch


def locate_deb(
    package_dir: Path, package_name: str, version: str, release: str
) -> Path:
    """Return the Debian package matching ``package_name`` and ``version``."""
    pattern = f"{package_name}_{version}-{release}_*.deb"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} deb package"
    )


def locate_rpm(
    package_dir: Path, package_name: str, version: str, release: str
) -> Path:
    """Return the RPM package matching ``package_name`` and ``version``."""
    pattern = f"{package_name}-{version}-{release}*.rpm"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} rpm package"
    )


def ensure_subset(
    expected: typ.Collection[str], actual: typ.Collection[str], label: str
) -> None:
    """Raise :class:`ValidationError` when expected items are missing."""
    if missing := [path for path in expected if path not in actual]:
        message = f"missing {label}: {', '.join(missing)}"
        raise ValidationError(message)


def _install_and_verify(
    sandbox_factory: SandboxFactory,
    package_path: Path,
    expected_paths: typ.Iterable[str],
    executable_paths: typ.Iterable[str],
    verify_command: tuple[str, ...],
    install_command: tuple[str, ...],
    remove_command: tuple[str, ...] | None,
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
            message = f"{install_error}: {exc}"
            raise ValidationError(message) from exc
        for path in expected_paths:
            sandbox.exec("test", "-e", path)
        for path in executable_paths:
            sandbox.exec("test", "-x", path)
        if verify_command:
            sandbox.exec(*verify_command)
        if remove_command is not None:
            try:
                sandbox.exec(*remove_command)
            except ProcessExecutionError as exc:
                logger.warning(
                    "suppressed exception during package removal: %s",
                    exc,
                )


def _validate_package(
    inspect_fn: typ.Callable[[Path], MetaT],
    *,
    package_path: Path,
    validators: typ.Iterable[typ.Callable[[MetaT], None]],
    expected_paths: typ.Collection[str],
    executable_paths: typ.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: SandboxFactory,
    payload_label: str,
    install_command: tuple[str, ...],
    remove_command: tuple[str, ...] | None,
    install_error: str,
) -> None:
    metadata = inspect_fn(package_path)
    for validator in validators:
        validator(metadata)
    ensure_subset(expected_paths, metadata.files, payload_label)

    _install_and_verify(
        sandbox_factory,
        package_path,
        expected_paths,
        executable_paths,
        verify_command,
        install_command=install_command,
        remove_command=remove_command,
        install_error=install_error,
    )


def validate_deb_package(
    dpkg_deb: BaseCommand,
    package_path: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_deb_version: str,
    expected_arch: str,
    expected_paths: typ.Collection[str],
    executable_paths: typ.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate Debian package metadata and sandbox installation."""

    def _validate_name(meta: DebMetadata) -> None:
        if meta.name != expected_name:
            _raise_validation("unexpected package name", expected_name, meta.name)

    def _validate_version(meta: DebMetadata) -> None:
        if meta.version not in {expected_deb_version, expected_version}:
            message = (
                "unexpected deb version: expected "
                f"{expected_deb_version!r} or {expected_version!r}, "
                f"found {meta.version!r}"
            )
            raise ValidationError(message)

    def _validate_arch(meta: DebMetadata) -> None:
        if meta.architecture != expected_arch:
            _raise_validation(
                "unexpected deb architecture",
                expected_arch,
                meta.architecture,
            )

    _validate_package(
        lambda pkg_path: inspect_deb_package(dpkg_deb, pkg_path),
        package_path=package_path,
        validators=(
            _validate_name,
            _validate_version,
            _validate_arch,
        ),
        expected_paths=expected_paths,
        executable_paths=executable_paths,
        verify_command=verify_command,
        sandbox_factory=sandbox_factory,
        payload_label="Debian package payload",
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
    expected_paths: typ.Collection[str],
    executable_paths: typ.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate RPM package metadata and sandbox installation."""
    acceptable_arches = acceptable_rpm_architectures(expected_arch)

    def _validate_release(meta: RpmMetadata) -> None:
        release = meta.release
        if release and not str(release).startswith(expected_release):
            message = (
                "unexpected rpm release: expected prefix "
                f"{expected_release!r}, found {release!r}"
            )
            raise ValidationError(message)

    def _validate_arch(meta: RpmMetadata) -> None:
        architecture = meta.architecture
        if architecture not in acceptable_arches:
            message = (
                "unexpected rpm architecture: expected one of "
                f"{sorted(acceptable_arches)!r}, found {architecture!r}"
            )
            raise ValidationError(message)

    def _validate_name(meta: RpmMetadata) -> None:
        if meta.name != expected_name:
            _raise_validation("unexpected package name", expected_name, meta.name)

    def _validate_version(meta: RpmMetadata) -> None:
        if meta.version != expected_version:
            _raise_validation("unexpected rpm version", expected_version, meta.version)

    _validate_package(
        lambda pkg_path: inspect_rpm_package(rpm_cmd, pkg_path),
        package_path=package_path,
        validators=(
            _validate_name,
            _validate_version,
            _validate_release,
            _validate_arch,
        ),
        expected_paths=expected_paths,
        executable_paths=executable_paths,
        verify_command=verify_command,
        sandbox_factory=sandbox_factory,
        payload_label="RPM package payload",
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


def _raise_validation(label: str, expected: str, actual: str) -> None:
    message = f"{label}: expected {expected!r}, found {actual!r}"
    raise ValidationError(message)
