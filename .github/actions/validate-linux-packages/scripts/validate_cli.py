"""CLI entrypoint for the validate-linux-packages action."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import tempfile
import typing as typ
from pathlib import Path

from cyclopts import App, Parameter
from cyclopts import config as cyclopts_config
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError
from validate_architecture import (
    UnsupportedTargetError,
    deb_arch_for_target,
    nfpm_arch_for_target,
)
from validate_exceptions import ValidationError
from validate_helpers import ensure_directory, ensure_exists, get_command
from validate_normalise import normalise_command, normalise_formats, normalise_paths
from validate_packages import (
    locate_deb,
    locate_rpm,
    rpm_expected_architecture,
    validate_deb_package,
    validate_rpm_package,
)
from validate_polythene import (
    PolytheneSession,
    default_polythene_command,
    polythene_rootfs,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helper import
    from plumbum.commands.base import BaseCommand
else:  # pragma: no cover - runtime helper fallbacks
    BaseCommand = typ.Any

__all__ = ["app", "main", "run"]

app = App()
_env_config = cyclopts_config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
app.config = (*tuple(existing_config), _env_config)


@dataclasses.dataclass(frozen=True, kw_only=True)
class ValidationInputs:
    """Raw CLI parameters collected before normalisation."""

    project_dir: Path | None = None
    package_name: str | None = None
    bin_name: str
    target: str = "x86_64-unknown-linux-gnu"
    version: str
    release: str | None = None
    arch: str | None = None
    formats: list[str] | None = None
    packages_dir: Path | None = None
    expected_paths: list[str] | None = None
    executable_paths: list[str] | None = None
    verify_command: list[str] | None = None
    deb_base_image: str = "docker.io/library/debian:bookworm"
    rpm_base_image: str = "docker.io/library/rockylinux:9"
    polythene_path: Path | None = None
    polythene_store: Path | None = None
    sandbox_timeout: str | None = None


def _optional_path(value: Path | None) -> Path | None:
    """Return ``None`` when ``value`` represents an empty CLI path."""
    if value is None:
        return None
    if value == Path():
        return None
    return value


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
    polythene_command: tuple[str, ...]
    timeout: int | None
    base_images: dict[str, str]

    @property
    def deb_version(self) -> str:
        """Return the Debian version string including release."""
        return f"{self.version}-{self.release}"


SandboxFactory = typ.Callable[[], typ.ContextManager["PolytheneSession"]]


def _handle_deb(
    command: BaseCommand,
    pkg_path: Path,
    cfg: ValidationConfig,
    sandbox_factory: SandboxFactory,
) -> None:
    validate_deb_package(
        command,
        pkg_path,
        expected_name=cfg.package_value,
        expected_version=cfg.version,
        expected_deb_version=cfg.deb_version,
        expected_arch=cfg.deb_arch,
        expected_paths=cfg.expected_paths,
        executable_paths=cfg.executable_paths,
        verify_command=cfg.verify_command,
        sandbox_factory=sandbox_factory,
    )
    print(f"✓ validated Debian package: {pkg_path}")


def _handle_rpm(
    command: BaseCommand,
    pkg_path: Path,
    cfg: ValidationConfig,
    sandbox_factory: SandboxFactory,
) -> None:
    validate_rpm_package(
        command,
        pkg_path,
        expected_name=cfg.package_value,
        expected_version=cfg.version,
        expected_release=cfg.release,
        expected_arch=rpm_expected_architecture(cfg.arch),
        expected_paths=cfg.expected_paths,
        executable_paths=cfg.executable_paths,
        verify_command=cfg.verify_command,
        sandbox_factory=sandbox_factory,
    )
    print(f"✓ validated RPM package: {pkg_path}")


_FORMAT_HANDLERS: dict[
    str,
    tuple[
        typ.Callable[[BaseCommand, Path, ValidationConfig, SandboxFactory], None],
        typ.Callable[[Path, str, str, str], Path],
        str,
    ],
] = {
    "deb": (
        _handle_deb,
        locate_deb,
        "dpkg-deb",
    ),
    "rpm": (
        _handle_rpm,
        locate_rpm,
        "rpm",
    ),
}


# Cyclopts maps each keyword-only parameter onto a distinct CLI flag; swapping
# the signature for a ``ValidationConfig`` argument would collapse the public
# interface into a single ``--config`` parameter and break existing automation.
# Keep the expanded signature and wrap the values in :class:`ValidationInputs`
# so :func:`_build_config` can translate them into a structured
# :class:`ValidationConfig` for downstream helpers.
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
    inputs = ValidationInputs(
        project_dir=project_dir,
        package_name=package_name,
        bin_name=bin_name,
        target=target,
        version=version,
        release=release,
        arch=arch,
        formats=formats,
        packages_dir=_optional_path(packages_dir),
        expected_paths=expected_paths,
        executable_paths=executable_paths,
        verify_command=verify_command,
        deb_base_image=deb_base_image,
        rpm_base_image=rpm_base_image,
        polythene_path=_optional_path(polythene_path),
        polythene_store=_optional_path(polythene_store),
        sandbox_timeout=sandbox_timeout,
    )

    config = _build_config(inputs)

    with _polythene_store(inputs.polythene_store) as store_base:
        for fmt in config.formats:
            store_dir = ensure_directory(store_base / fmt)
            _validate_format(fmt, config, store_dir)


app.command()(main)
app.default(main)


def run() -> None:
    """Entry point for script execution."""
    app()


def _build_config(inputs: ValidationInputs) -> ValidationConfig:
    """Derive configuration from CLI parameters."""
    project_root = (
        Path(inputs.project_dir) if inputs.project_dir is not None else Path.cwd()
    )
    packages_dir_value = inputs.packages_dir or (project_root / "dist")
    ensure_exists(packages_dir_value, "package directory not found")

    bin_value = inputs.bin_name.strip()
    if not bin_value:
        message = "bin-name input is required"
        raise ValidationError(message)

    package_value = (inputs.package_name or bin_value).strip() or bin_value
    version_value = inputs.version.strip().lstrip("v")
    if not version_value:
        message = "version input is required"
        raise ValidationError(message)

    release_value = (inputs.release or "1").strip() or "1"
    target_value = inputs.target.strip() or "x86_64-unknown-linux-gnu"
    try:
        timeout_value = int(inputs.sandbox_timeout) if inputs.sandbox_timeout else None
    except ValueError as exc:
        message = (
            f"sandbox_timeout must be an integer, received {inputs.sandbox_timeout!r}"
        )
        raise ValidationError(message) from exc

    try:
        arch_value = (inputs.arch or nfpm_arch_for_target(target_value)).strip()
    except UnsupportedTargetError as exc:
        message = f"unsupported target triple: {target_value}"
        raise ValidationError(message) from exc

    deb_arch_value = deb_arch_for_target(target_value)

    formats_value = tuple(normalise_formats(inputs.formats))
    if not formats_value:
        message = "no package formats provided"
        raise ValidationError(message)

    expected_paths_value, executable_paths_value = _prepare_paths(
        bin_value, inputs.expected_paths, inputs.executable_paths
    )
    verify_tuple = tuple(normalise_command(inputs.verify_command))

    polythene_path = inputs.polythene_path
    if polythene_path is None:
        polythene_command = default_polythene_command()
    else:
        # The helper is launched via ``uv run`` which reads the script directly,
        # so the file only needs to exist and does not require the executable bit.
        if not polythene_path.exists():
            message = f"polythene script not found: {polythene_path}"
            raise ValidationError(message)
        try:
            with polythene_path.open("r"):
                pass
        except PermissionError as exc:
            message = (
                "polythene script is not readable due to permission error: "
                f"{polythene_path}"
            )
            raise ValidationError(message) from exc
        except OSError as exc:
            message = f"polythene script could not be read: {polythene_path} ({exc})"
            raise ValidationError(message) from exc
        polythene_command = (polythene_path.as_posix(),)

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
        polythene_command=polythene_command,
        timeout=timeout_value,
        base_images={
            "deb": inputs.deb_base_image,
            "rpm": inputs.rpm_base_image,
        },
    )


def _prepare_paths(
    bin_value: str,
    expected_paths: list[str] | None,
    executable_paths: list[str] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return normalised expected and executable paths.

    The default ``/usr/bin/{bin_value}`` entry is always injected into
    ``expected_paths`` when missing. Entries provided via ``executable_paths``
    are appended to ``expected_paths`` so every executable is verified during
    sandbox checks.
    """
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


def _supports_executable_stores(base: Path) -> Path | None:
    """Return ``base`` when the filesystem allows executing files."""
    try:
        candidate = ensure_directory(base).resolve()
    except OSError:
        return None

    if os.name != "posix":  # pragma: no cover - non-POSIX runners
        return candidate

    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="polythene-validate-probe-",
            dir=candidate,
            delete=False,
        ) as tmp:
            probe_path = Path(tmp.name)
            tmp.write("#!/bin/sh\nexit 0\n")

        probe_path.chmod(0o700)
        local[probe_path.as_posix()]()
    except (OSError, ProcessExecutionError):
        return None
    finally:
        if probe_path is not None:
            with contextlib.suppress(OSError):
                probe_path.unlink()

    return candidate


def ensure_executable_store(path: Path) -> Path:
    """Ensure ``path`` resides on an executable filesystem."""
    candidate = _supports_executable_stores(path)
    if candidate is not None:
        return candidate

    message = (
        "polythene-store must be located on an executable filesystem; "
        f"{path} does not allow running binaries. "
        "Choose a directory under $GITHUB_WORKSPACE or another exec-mounted path."
    )
    raise ValidationError(message)


def _find_executable_candidate() -> Path | None:
    """Find an executable filesystem candidate from environment variables."""
    for env_var in ("GITHUB_WORKSPACE", "RUNNER_TEMP"):
        location = os.environ.get(env_var)
        if not location:
            continue

        candidate = _supports_executable_stores(Path(location))
        if candidate is not None:
            return candidate

    return None


@contextlib.contextmanager
def _polythene_store(polythene_store: Path | None) -> typ.Iterator[Path]:
    """Yield a base directory for polythene store usage."""
    if polythene_store:
        store_base = ensure_executable_store(polythene_store.resolve())
        yield store_base
        return

    candidate = _find_executable_candidate()
    if candidate is not None:
        try:
            with tempfile.TemporaryDirectory(
                prefix="polythene-validate-",
                dir=candidate,
            ) as tmp:
                yield ensure_executable_store(Path(tmp))
                return
        except OSError:
            pass

    with tempfile.TemporaryDirectory(prefix="polythene-validate-") as tmp:
        yield ensure_executable_store(Path(tmp))


def _validate_format(fmt: str, config: ValidationConfig, store_dir: Path) -> None:
    """Dispatch validation for ``fmt`` using ``config`` settings."""
    try:
        validate_fn, locate_fn, cmd_name = _FORMAT_HANDLERS[fmt]
    except KeyError as exc:
        message = f"unsupported package format: {fmt}"
        raise ValidationError(message) from exc

    image = config.base_images.get(fmt)
    if image is None:
        message = f"unsupported package format: {fmt}"
        raise ValidationError(message)

    command = get_command(cmd_name)
    package_path = locate_fn(
        config.packages_dir,
        config.package_value,
        config.version,
        config.release,
    )

    def sandbox_factory() -> typ.ContextManager["PolytheneSession"]:  # noqa: UP037
        return polythene_rootfs(
            config.polythene_command,
            image,
            store_dir,
            timeout=config.timeout,
        )

    validate_fn(command, package_path, config, sandbox_factory)
