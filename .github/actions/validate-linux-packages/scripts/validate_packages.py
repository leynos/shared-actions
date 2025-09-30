"""Package validation helpers for the validate-linux-packages action."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Collection, ContextManager, Iterable, Protocol, Tuple, TypeVar

from plumbum.commands.base import BaseCommand
from plumbum.commands.processes import ProcessExecutionError

from script_utils import ensure_directory, unique_match

from validate_exceptions import ValidationError
from validate_metadata import DebMetadata, RpmMetadata, inspect_deb_package, inspect_rpm_package
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
MetaT = TypeVar("MetaT", bound="_SupportsFiles")


class _SupportsFiles(Protocol):
    files: Collection[str]


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

    if missing := [path for path in expected if path not in actual]:
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
            try:
                sandbox.exec(*remove_command)
            except ProcessExecutionError as exc:
                print(f"Suppressed exception during package removal: {exc}")


def _validate_package(
    inspect_fn: Callable[[Path], MetaT],
    *,
    package_path: Path,
    validators: Iterable[Callable[[MetaT], None]],
    expected_paths: Collection[str],
    executable_paths: Collection[str],
    verify_command: Tuple[str, ...],
    sandbox_factory: SandboxFactory,
    payload_label: str,
    install_command: Tuple[str, ...],
    remove_command: Tuple[str, ...] | None,
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
    expected_paths: Collection[str],
    executable_paths: Collection[str],
    verify_command: Tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate Debian package metadata and sandbox installation."""
    def _validate_name(meta: DebMetadata) -> None:
        if meta.name != expected_name:
            _raise_validation("unexpected package name", expected_name, meta.name)

    def _validate_version(meta: DebMetadata) -> None:
        if meta.version not in {expected_deb_version, expected_version}:
            _raise_validation("unexpected deb version", expected_deb_version, meta.version)

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
    expected_paths: Collection[str],
    executable_paths: Collection[str],
    verify_command: Tuple[str, ...],
    sandbox_factory: SandboxFactory,
) -> None:
    """Validate RPM package metadata and sandbox installation."""
    acceptable_arches = acceptable_rpm_architectures(expected_arch)

    def _validate_release(meta: RpmMetadata) -> None:
        release = getattr(meta, "release", "")
        if release and not str(release).startswith(expected_release):
            raise ValidationError(
                "unexpected rpm release: expected prefix "
                f"{expected_release!r}, found {release!r}"
            )

    def _validate_arch(meta: RpmMetadata) -> None:
        architecture = getattr(meta, "architecture")
        if architecture not in acceptable_arches:
            raise ValidationError(
                "unexpected rpm architecture: expected one of "
                f"{sorted(acceptable_arches)!r}, found {architecture!r}"
            )

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
    raise ValidationError(
        f"{label}: expected {expected!r}, found {actual!r}"
    )
