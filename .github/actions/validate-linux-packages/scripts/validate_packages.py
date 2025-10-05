"""Package validation helpers for the validate-linux-packages action."""

from __future__ import annotations

import logging
import pathlib
import platform
import typing as typ

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
    "DebMetadata",
    "RpmMetadata",
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


_HOST_ARCH_ALIAS_MAP: dict[str, set[str]] = {
    "x86_64": {"x86_64", "amd64"},
    "amd64": {"x86_64", "amd64"},
    "aarch64": {"aarch64", "arm64"},
    "arm64": {"aarch64", "arm64"},
    "armv7l": {"armv7l", "armhf"},
    "armv6l": {"armv6l", "armhf"},
    "ppc64le": {"ppc64le"},
    "s390x": {"s390x"},
    "riscv64": {"riscv64"},
    "loongarch64": {"loongarch64", "loong64"},
    "loong64": {"loongarch64", "loong64"},
}


def _host_architectures() -> set[str]:
    """Return aliases for the host processor architecture."""
    machine = (platform.machine() or "").lower()
    if not machine:
        return set()
    aliases = _HOST_ARCH_ALIAS_MAP.get(machine, {machine})
    return {alias.lower() for alias in aliases}


def _should_skip_sandbox(package_architecture: str | None) -> bool:
    """Return ``True`` when sandbox checks should be skipped for the architecture."""
    if not package_architecture:
        return False
    normalized = package_architecture.lower()
    if normalized in {"all", "any", "noarch"}:
        return False

    host_arches = _host_architectures()
    if not host_arches:
        return False
    return normalized not in host_arches


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

        sandbox_path = f"/{package_path.name}"

        def _exec_with_context(
            *args: str, context: str, timeout: int | None = None
        ) -> str:
            try:
                return sandbox.exec(*args, timeout=timeout)
            except ValidationError as exc:
                message = f"{context}: {exc}"
                raise ValidationError(message) from exc

        install_args = tuple(
            sandbox_path if arg == package_path.name else arg for arg in install_command
        )

        _exec_with_context(*install_args, context=install_error)

        for path in expected_paths:
            _exec_with_context(
                "test",
                "-e",
                path,
                context=f"expected path missing from sandbox payload: {path}",
            )
        for path in executable_paths:
            _exec_with_context(
                "test",
                "-x",
                path,
                context=f"expected path is not executable: {path}",
            )
        if verify_command:
            _exec_with_context(
                *verify_command,
                context="sandbox verify command failed",
            )
        if remove_command is not None:
            try:
                sandbox.exec(*remove_command)
            except ValidationError as exc:
                logger.warning(
                    "suppressed exception during package removal: %s",
                    exc,
                )


class _MetadataValidators:
    """Factory helpers for metadata validation callables."""

    @staticmethod
    def raise_error(label: str, expected: str, actual: str) -> None:
        message = f"{label}: expected {expected!r}, found {actual!r}"
        raise ValidationError(message)

    @staticmethod
    def equal(attr: str, expected: str, label: str) -> typ.Callable[[MetaT], None]:
        def _validator(meta: MetaT) -> None:
            actual = getattr(meta, attr)
            if actual != expected:
                _MetadataValidators.raise_error(label, expected, actual)

        return _validator

    @staticmethod
    def in_set(
        attr: str,
        expected: typ.Collection[str],
        label: str,
        *,
        fmt_expected: typ.Callable[[typ.Collection[str]], str] | None = None,
    ) -> typ.Callable[[MetaT], None]:
        formatter = fmt_expected or (
            lambda values: f"one of [{', '.join(sorted(values))}]"
        )

        def _validator(meta: MetaT) -> None:
            actual = getattr(meta, attr)
            if actual not in expected:
                _MetadataValidators.raise_error(label, formatter(expected), actual)

        return _validator

    @staticmethod
    def prefix(attr: str, prefix: str, label: str) -> typ.Callable[[MetaT], None]:
        def _validator(meta: MetaT) -> None:
            value = getattr(meta, attr)
            text = "" if value is None else str(value)
            if text and not text.startswith(prefix):
                _MetadataValidators.raise_error(
                    label, f"starting with {prefix!r}", text
                )

        return _validator


def _validate_package(
    inspect_fn: typ.Callable[[Path], MetaT],
    *,
    package_path: Path,
    validators: typ.Iterable[typ.Callable[[MetaT], None]],
    architecture_validator: typ.Callable[[MetaT], None] | None = None,
    expected_paths: typ.Collection[str],
    executable_paths: typ.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: SandboxFactory,
    payload_label: str,
    install_command: tuple[str, ...],
    remove_command: tuple[str, ...] | None,
    install_error: str,
    package_architecture: str | None = None,
    package_format: str | None = None,
) -> None:
    metadata = inspect_fn(package_path)
    for validator in validators:
        validator(metadata)
    ensure_subset(expected_paths, metadata.files, payload_label)

    metadata_architecture = getattr(metadata, "architecture", None)

    if _should_skip_sandbox(metadata_architecture):
        host_machine = platform.machine() or "unknown"
        format_label = f"{package_format} package" if package_format else "package"
        actual_arch = metadata_architecture or "unknown"
        expected_arch = package_architecture or "unspecified"
        logger.info(
            "skipping %s sandbox validation: package architecture %s "
            "(expected %s) is not supported on host %s",
            format_label,
            actual_arch,
            expected_arch,
            host_machine,
        )
        return

    if architecture_validator is not None:
        architecture_validator(metadata)

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
    _validate_package(
        lambda pkg_path: inspect_deb_package(dpkg_deb, pkg_path),
        package_path=package_path,
        validators=(
            _MetadataValidators.equal("name", expected_name, "unexpected package name"),
            _MetadataValidators.in_set(
                "version",
                {expected_deb_version, expected_version},
                "unexpected deb version",
                fmt_expected=lambda values: (
                    f"{expected_deb_version!r} or {expected_version!r}"
                ),
            ),
        ),
        architecture_validator=_MetadataValidators.equal(
            "architecture", expected_arch, "unexpected deb architecture"
        ),
        expected_paths=expected_paths,
        executable_paths=executable_paths,
        verify_command=verify_command,
        sandbox_factory=sandbox_factory,
        payload_label="Debian package payload",
        install_command=("dpkg", "-i", package_path.name),
        remove_command=("dpkg", "-r", expected_name),
        install_error="dpkg installation failed",
        package_architecture=expected_arch,
        package_format="deb",
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

    architecture_validator = _MetadataValidators.in_set(
        "architecture",
        acceptable_arches,
        "unexpected rpm architecture",
    )

    _validate_package(
        lambda pkg_path: inspect_rpm_package(rpm_cmd, pkg_path),
        package_path=package_path,
        validators=(
            _MetadataValidators.equal("name", expected_name, "unexpected package name"),
            _MetadataValidators.equal(
                "version", expected_version, "unexpected rpm version"
            ),
            _MetadataValidators.prefix(
                "release", expected_release, "unexpected rpm release prefix"
            ),
        ),
        architecture_validator=architecture_validator,
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
        package_architecture=expected_arch,
        package_format="rpm",
    )
