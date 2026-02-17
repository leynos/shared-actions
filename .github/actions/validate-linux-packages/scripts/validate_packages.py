"""Package validation helpers for the validate-linux-packages action."""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import platform
import shutil
import typing as typ

from validate_arch import (
    _should_skip_sandbox,
    acceptable_rpm_architectures,
    rpm_expected_architecture,
)
from validate_exceptions import ValidationError
from validate_formatters import _extract_process_stderr
from validate_helpers import ensure_directory
from validate_locators import ensure_subset, locate_deb, locate_rpm
from validate_metadata import (
    DebMetadata,
    RpmMetadata,
    inspect_deb_package,
    inspect_rpm_package,
)
from validate_path_checks import _validate_paths_executable, _validate_paths_exist
from validate_polythene import PolytheneSession
from validate_sandbox_diagnostics import (
    _collect_diagnostics_safely,
    _collect_environment_details,
    _collect_host_path_details,
)

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

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from pathlib import Path

    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime fallbacks
    Path = pathlib.Path
    BaseCommand = object

SandboxFactory = typ.Callable[[], typ.ContextManager[PolytheneSession]]
MetaT = typ.TypeVar("MetaT", bound="_SupportsFiles")


class _SupportsFiles(typ.Protocol):
    files: typ.Collection[str]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ValidationPaths:
    """Paths to validate during package installation."""

    expected_paths: tuple[str, ...]
    executable_paths: tuple[str, ...]
    verify_command: tuple[str, ...]


@dataclasses.dataclass(frozen=True, kw_only=True)
class InstallConfig:
    """Configuration for package installation and removal."""

    install_command: tuple[str, ...]
    remove_command: tuple[str, ...] | None
    install_error: str


@dataclasses.dataclass(frozen=True, kw_only=True)
class PackageContext:
    """Package-specific context for validation."""

    payload_label: str
    package_architecture: str | None = None
    package_format: str | None = None


def _exec_with_diagnostics(
    sandbox: PolytheneSession,
    args: tuple[str, ...],
    context: str,
    timeout: int | None = None,
    diagnostics_fn: typ.Callable[[BaseException | None], str | None] | None = None,
) -> str:
    """Run ``sandbox.exec`` while enriching ``ValidationError`` messages."""
    try:
        return sandbox.exec(*args, timeout=timeout)
    except ValidationError as exc:
        cause = exc.__cause__
        stderr_detail = _extract_process_stderr(cause)

        detail = _collect_diagnostics_safely(diagnostics_fn, args, cause)

        message = f"{context}: {exc}"
        if stderr_detail:
            message = f"{message}\nstderr: {stderr_detail}"
        if detail:
            message = f"{message}\n{detail}"
        raise ValidationError(message) from exc


def _install_and_verify(
    sandbox_factory: SandboxFactory,
    package_path: Path,
    paths: ValidationPaths,
    install_cfg: InstallConfig,
) -> None:
    expected_paths = paths.expected_paths
    executable_paths = paths.executable_paths

    with sandbox_factory() as sandbox:
        dest = sandbox.root / package_path.name
        ensure_directory(dest.parent)

        with package_path.open("rb") as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)

        sandbox_path = f"/{package_path.name}"

        logger.info(
            "Using sandbox isolation %s for %s",
            sandbox.isolation or "default",
            sandbox.root,
        )

        def _exec_with_context(
            *args: str,
            context: str,
            timeout: int | None = None,
            diagnostics_fn: typ.Callable[[BaseException | None], str | None]
            | None = None,
        ) -> str:
            return _exec_with_diagnostics(
                sandbox,
                args,
                context,
                timeout,
                diagnostics_fn,
            )

        install_args = tuple(
            sandbox_path if arg == package_path.name else arg
            for arg in install_cfg.install_command
        )

        _exec_with_context(*install_args, context=install_cfg.install_error)

        env_details = _collect_environment_details(sandbox)

        if env_details:
            logger.info("Sandbox context: %s", "; ".join(env_details))

        combined_paths = list(dict.fromkeys((*expected_paths, *executable_paths)))
        host_details = _collect_host_path_details(sandbox, combined_paths)

        if host_details:
            logger.info("Host view of sandbox paths: %s", "; ".join(host_details))

        _validate_paths_exist(sandbox, expected_paths, _exec_with_context)
        _validate_paths_executable(sandbox, executable_paths, _exec_with_context)
        if paths.verify_command:
            _exec_with_context(
                *paths.verify_command,
                context="sandbox verify command failed",
            )
        if install_cfg.remove_command is not None:
            try:
                sandbox.exec(*install_cfg.remove_command)
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


def _validate_package[MetaT: _SupportsFiles](
    inspect_fn: typ.Callable[[Path], MetaT],
    *,
    package_path: Path,
    validators: typ.Iterable[typ.Callable[[MetaT], None]],
    architecture_validator: typ.Callable[[MetaT], None] | None = None,
    paths: ValidationPaths,
    install_cfg: InstallConfig,
    context: PackageContext,
    sandbox_factory: SandboxFactory,
) -> None:
    metadata = inspect_fn(package_path)
    for validator in validators:
        validator(metadata)
    ensure_subset(paths.expected_paths, metadata.files, context.payload_label)

    metadata_architecture = getattr(metadata, "architecture", None)

    if _should_skip_sandbox(metadata_architecture):
        host_machine = platform.machine() or "unknown"
        format_label = (
            f"{context.package_format} package" if context.package_format else "package"
        )
        actual_arch = metadata_architecture or "unknown"
        expected_arch = context.package_architecture or "unspecified"
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
        paths,
        install_cfg,
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
    paths = ValidationPaths(
        expected_paths=tuple(expected_paths),
        executable_paths=tuple(executable_paths),
        verify_command=verify_command,
    )
    install_cfg = InstallConfig(
        install_command=("dpkg", "-i", package_path.name),
        remove_command=("dpkg", "-r", expected_name),
        install_error="dpkg installation failed",
    )
    context = PackageContext(
        payload_label="Debian package payload",
        package_architecture=expected_arch,
        package_format="deb",
    )

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
        paths=paths,
        install_cfg=install_cfg,
        context=context,
        sandbox_factory=sandbox_factory,
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
    paths = ValidationPaths(
        expected_paths=tuple(expected_paths),
        executable_paths=tuple(executable_paths),
        verify_command=verify_command,
    )
    install_cfg = InstallConfig(
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
    context = PackageContext(
        payload_label="RPM package payload",
        package_architecture=expected_arch,
        package_format="rpm",
    )

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
        paths=paths,
        install_cfg=install_cfg,
        context=context,
        sandbox_factory=sandbox_factory,
    )
