"""CLI entrypoint for the validate-linux-packages action."""

from __future__ import annotations

import contextlib
import tempfile
import typing as typ
from dataclasses import dataclass
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

from architectures import UnsupportedTargetError, deb_arch_for_target, nfpm_arch_for_target
from script_utils import ensure_directory, ensure_exists, get_command

from validate_exceptions import ValidationError
from validate_normalise import normalise_command, normalise_formats, normalise_paths
from validate_packages import (
    locate_deb,
    locate_rpm,
    validate_deb_package,
    validate_rpm_package,
)
from validate_polythene import default_polythene_path, polythene_rootfs

__all__ = ["app", "main", "run"]

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
app.config = (*tuple(existing_config), _env_config)


@dataclass(frozen=True)
class ValidationConfig:
    """Container for derived settings used during validation."""

    package_dir: Path
    package_value: str
    version: str
    release: str
    arch: str
    deb_arch: str
    formats: tuple[str, ...]
    expected_paths: tuple[str, ...]
    executable_paths: tuple[str, ...]
    verify_command: tuple[str, ...]
    polythene_script: Path
    timeout: int | None
    base_images: dict[str, str]

    @property
    def deb_version(self) -> str:
        """Return the Debian version string including release."""

        return f"{self.version}-{self.release}"


def main(
    *,
    project_dir: Path = Path("."),
    package_name: str | None = None,
    bin_name: typ.Annotated[str, Parameter(required=True)],
    target: str = "x86_64-unknown-linux-gnu",
    version: typ.Annotated[str, Parameter(required=True)],
    release: str | None = None,
    arch: str | None = None,
    formats: list[str] | None = None,
    package_dir: Path | None = None,
    expected_paths: list[str] | None = None,
    executable_paths: list[str] | None = None,
    verify_command: list[str] | None = None,
    deb_base_image: str = "docker.io/library/debian:bookworm",
    rpm_base_image: str = "docker.io/library/rockylinux:9",
    polythene_path: Path | None = None,
    polythene_store: Path | None = None,
    sandbox_timeout: str | None = None,
) -> None:
    """Validate the requested Linux packages."""

    config = _build_config(
        project_dir=project_dir,
        package_name=package_name,
        bin_name=bin_name,
        target=target,
        version=version,
        release=release,
        arch=arch,
        formats=formats,
        package_dir=package_dir,
        expected_paths=expected_paths,
        executable_paths=executable_paths,
        verify_command=verify_command,
        polythene_path=polythene_path,
        sandbox_timeout=sandbox_timeout,
        deb_base_image=deb_base_image,
        rpm_base_image=rpm_base_image,
    )

    with _polythene_store(polythene_store) as store_base:
        for fmt in config.formats:
            store_dir = ensure_directory(store_base / fmt)
            _validate_format(fmt, config, store_dir)


def run() -> None:
    """Entry point for script execution."""

    app()


def _build_config(
    *,
    project_dir: Path,
    package_name: str | None,
    bin_name: str,
    target: str,
    version: str,
    release: str | None,
    arch: str | None,
    formats: list[str] | None,
    package_dir: Path | None,
    expected_paths: list[str] | None,
    executable_paths: list[str] | None,
    verify_command: list[str] | None,
    polythene_path: Path | None,
    sandbox_timeout: str | None,
    deb_base_image: str,
    rpm_base_image: str,
) -> ValidationConfig:
    """Derive configuration from CLI parameters."""

    project_dir = project_dir or Path(".")
    package_dir_value = package_dir or (project_dir / "dist")
    ensure_exists(package_dir_value, "package directory not found")

    bin_value = bin_name.strip()
    if not bin_value:
        raise ValidationError("bin-name input is required")

    package_value = (package_name or bin_value).strip() or bin_value
    version_value = version.strip().lstrip("v")
    if not version_value:
        raise ValidationError("version input is required")

    release_value = (release or "1").strip() or "1"
    target_value = target.strip() or "x86_64-unknown-linux-gnu"
    timeout_value = int(sandbox_timeout) if sandbox_timeout else None

    try:
        arch_value = (arch or nfpm_arch_for_target(target_value)).strip()
    except UnsupportedTargetError as exc:
        raise ValidationError(f"unsupported target triple: {target_value}") from exc

    deb_arch_value = deb_arch_for_target(target_value)

    formats_value = tuple(normalise_formats(formats))
    if not formats_value:
        raise ValidationError("no package formats provided")

    expected_paths_value, executable_paths_value = _prepare_paths(
        bin_value, expected_paths, executable_paths
    )
    verify_tuple = tuple(normalise_command(verify_command))

    polythene_script = polythene_path or default_polythene_path()
    if not polythene_script.exists():
        raise ValidationError(f"polythene script not found: {polythene_script}")

    return ValidationConfig(
        package_dir=package_dir_value,
        package_value=package_value,
        version=version_value,
        release=release_value,
        arch=arch_value,
        deb_arch=deb_arch_value,
        formats=formats_value,
        expected_paths=expected_paths_value,
        executable_paths=executable_paths_value,
        verify_command=verify_tuple,
        polythene_script=polythene_script,
        timeout=timeout_value,
        base_images={"deb": deb_base_image, "rpm": rpm_base_image},
    )


def _prepare_paths(
    bin_value: str,
    expected_paths: list[str] | None,
    executable_paths: list[str] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return normalised expected and executable paths."""

    default_binary_path = f"/usr/bin/{bin_value}"
    expected_paths_list = normalise_paths(expected_paths)
    if not expected_paths_list:
        expected_paths_list = [default_binary_path]
    elif default_binary_path not in expected_paths_list:
        expected_paths_list.insert(0, default_binary_path)

    executable_paths_list = normalise_paths(executable_paths)
    if not executable_paths_list:
        executable_paths_list = [default_binary_path]
    else:
        for entry in executable_paths_list:
            if entry not in expected_paths_list:
                expected_paths_list.append(entry)

    return tuple(expected_paths_list), tuple(executable_paths_list)


@contextlib.contextmanager
def _polythene_store(polythene_store: Path | None) -> typ.Iterator[Path]:
    """Yield a base directory for polythene store usage."""

    if polythene_store:
        store_base = polythene_store.resolve()
        ensure_directory(store_base)
        yield store_base
        return

    with tempfile.TemporaryDirectory(prefix="polythene-validate-") as tmp:
        yield Path(tmp)


def _validate_format(fmt: str, config: ValidationConfig, store_dir: Path) -> None:
    """Dispatch validation for ``fmt`` using ``config`` settings."""

    image = config.base_images.get(fmt)
    if image is None:
        raise ValidationError(f"unsupported package format: {fmt}")

    sandbox_factory = lambda image=image, directory=store_dir: polythene_rootfs(  # noqa: E731
        config.polythene_script,
        image,
        directory,
        timeout=config.timeout,
    )

    if fmt == "deb":
        command = get_command("dpkg-deb")
        package_path = locate_deb(
            config.package_dir,
            config.package_value,
            config.version,
            config.release,
        )
        validate_deb_package(
            command,
            package_path,
            expected_name=config.package_value,
            expected_version=config.version,
            expected_deb_version=config.deb_version,
            expected_arch=config.deb_arch,
            expected_paths=config.expected_paths,
            executable_paths=config.executable_paths,
            verify_command=config.verify_command,
            sandbox_factory=sandbox_factory,
        )
        print(f"✓ validated Debian package: {package_path}")
        return

    if fmt == "rpm":
        command = get_command("rpm")
        package_path = locate_rpm(
            config.package_dir,
            config.package_value,
            config.version,
            config.release,
        )
        validate_rpm_package(
            command,
            package_path,
            expected_name=config.package_value,
            expected_version=config.version,
            expected_release=config.release,
            expected_arch=config.arch,
            expected_paths=config.expected_paths,
            executable_paths=config.executable_paths,
            verify_command=config.verify_command,
            sandbox_factory=sandbox_factory,
        )
        print(f"✓ validated RPM package: {package_path}")
        return

    raise ValidationError(f"unsupported package format: {fmt}")
