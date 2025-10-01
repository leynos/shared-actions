"""CLI entrypoint for the validate-linux-packages action."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import sys
import tempfile
import typing as typ
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SIBLING_SCRIPTS = SCRIPT_DIR.parent.parent / "linux-packages" / "scripts"
for candidate in (SCRIPT_DIR, SIBLING_SCRIPTS):
    location = str(candidate)
    if location not in sys.path:
        sys.path.append(location)

cyclopts = importlib.import_module("cyclopts")
App = cyclopts.App
Parameter = cyclopts.Parameter

architectures = importlib.import_module("architectures")
UnsupportedTargetError = architectures.UnsupportedTargetError
deb_arch_for_target = architectures.deb_arch_for_target
nfpm_arch_for_target = architectures.nfpm_arch_for_target

script_utils = importlib.import_module("script_utils")
ensure_directory = script_utils.ensure_directory
ensure_exists = script_utils.ensure_exists
get_command = script_utils.get_command

validate_exceptions = importlib.import_module("validate_exceptions")
ValidationError = validate_exceptions.ValidationError

normalise_module = importlib.import_module("validate_normalise")
normalise_command = normalise_module.normalise_command
normalise_formats = normalise_module.normalise_formats
normalise_paths = normalise_module.normalise_paths

packages_module = importlib.import_module("validate_packages")
locate_deb = packages_module.locate_deb
locate_rpm = packages_module.locate_rpm
rpm_expected_architecture = packages_module.rpm_expected_architecture
validate_deb_package = packages_module.validate_deb_package
validate_rpm_package = packages_module.validate_rpm_package

polythene_module = importlib.import_module("validate_polythene")
default_polythene_path = polythene_module.default_polythene_path
polythene_rootfs = polythene_module.polythene_rootfs

__all__ = ["app", "main", "run"]

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
app.config = (*tuple(existing_config), _env_config)


@dataclasses.dataclass(frozen=True)
class ValidationConfig:
    """Container for derived settings used during validation."""

    packages_dir: Path
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
    project_dir: Path | None = None,
    package_name: str | None = None,
    bin_name: typ.Annotated[str, Parameter(required=True)],
    target: str = "x86_64-unknown-linux-gnu",
    version: typ.Annotated[str, Parameter(required=True)],
    release: str | None = None,
    arch: str | None = None,
    formats: list[str] | None = None,
    packages_dir: Path | None = None,
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
        packages_dir=packages_dir,
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
    project_dir: Path | None,
    package_name: str | None,
    bin_name: str,
    target: str,
    version: str,
    release: str | None,
    arch: str | None,
    formats: list[str] | None,
    packages_dir: Path | None,
    expected_paths: list[str] | None,
    executable_paths: list[str] | None,
    verify_command: list[str] | None,
    polythene_path: Path | None,
    sandbox_timeout: str | None,
    deb_base_image: str,
    rpm_base_image: str,
) -> ValidationConfig:
    """Derive configuration from CLI parameters."""
    project_root = Path(project_dir) if project_dir is not None else Path.cwd()
    packages_dir_value = packages_dir or (project_root / "dist")
    ensure_exists(packages_dir_value, "package directory not found")

    bin_value = bin_name.strip()
    if not bin_value:
        message = "bin-name input is required"
        raise ValidationError(message)

    package_value = (package_name or bin_value).strip() or bin_value
    version_value = version.strip().lstrip("v")
    if not version_value:
        message = "version input is required"
        raise ValidationError(message)

    release_value = (release or "1").strip() or "1"
    target_value = target.strip() or "x86_64-unknown-linux-gnu"
    timeout_value = int(sandbox_timeout) if sandbox_timeout else None

    try:
        arch_value = (arch or nfpm_arch_for_target(target_value)).strip()
    except UnsupportedTargetError as exc:
        message = f"unsupported target triple: {target_value}"
        raise ValidationError(message) from exc

    deb_arch_value = deb_arch_for_target(target_value)

    formats_value = tuple(normalise_formats(formats))
    if not formats_value:
        message = "no package formats provided"
        raise ValidationError(message)

    expected_paths_value, executable_paths_value = _prepare_paths(
        bin_value, expected_paths, executable_paths
    )
    verify_tuple = tuple(normalise_command(verify_command))

    polythene_script = polythene_path or default_polythene_path()
    if not polythene_script.exists():
        message = f"polythene script not found: {polythene_script}"
        raise ValidationError(message)

    return ValidationConfig(
        packages_dir=packages_dir_value,
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
        message = f"unsupported package format: {fmt}"
        raise ValidationError(message)

    sandbox_factory = lambda image=image, directory=store_dir: polythene_rootfs(  # noqa: E731
        config.polythene_script,
        image,
        directory,
        timeout=config.timeout,
    )

    if fmt == "deb":
        command = get_command("dpkg-deb")
        package_path = locate_deb(
            config.packages_dir,
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
            config.packages_dir,
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
            expected_arch=rpm_expected_architecture(config.arch),
            expected_paths=config.expected_paths,
            executable_paths=config.executable_paths,
            verify_command=config.verify_command,
            sandbox_factory=sandbox_factory,
        )
        print(f"✓ validated RPM package: {package_path}")
        return

    message = f"unsupported package format: {fmt}"
    raise ValidationError(message)
