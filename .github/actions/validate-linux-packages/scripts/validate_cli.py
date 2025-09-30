"""CLI entrypoint for the validate-linux-packages action."""

from __future__ import annotations

import contextlib
import tempfile
import typing as typ
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

    format_list = normalise_formats(formats)
    if not format_list:
        raise ValidationError("no package formats provided")

    expected_paths_list = normalise_paths(expected_paths)
    default_binary_path = f"/usr/bin/{bin_value}"
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

    verify_tuple = tuple(normalise_command(verify_command))

    polythene_script = polythene_path or default_polythene_path()
    if not polythene_script.exists():
        raise ValidationError(f"polythene script not found: {polythene_script}")

    with contextlib.ExitStack() as stack:
        if polythene_store:
            store_base = polythene_store.resolve()
            ensure_directory(store_base)
        else:
            tmp_store = stack.enter_context(
                tempfile.TemporaryDirectory(prefix="polythene-validate-")
            )
            store_base = Path(tmp_store)

        for fmt in format_list:
            store_dir = ensure_directory(store_base / fmt)
            if fmt == "deb":
                dpkg_deb = get_command("dpkg-deb")
                package_path = locate_deb(
                    package_dir_value, package_value, version_value, release_value
                )
                sandbox_factory = lambda image=deb_base_image, directory=store_dir: polythene_rootfs(  # noqa: E731
                    polythene_script,
                    image,
                    directory,
                    timeout=timeout_value,
                )
                validate_deb_package(
                    dpkg_deb,
                    package_path,
                    expected_name=package_value,
                    expected_version=version_value,
                    expected_deb_version=f"{version_value}-{release_value}",
                    expected_arch=deb_arch_value,
                    expected_paths=expected_paths_list,
                    executable_paths=executable_paths_list,
                    verify_command=verify_tuple,
                    sandbox_factory=sandbox_factory,
                )
                print(f"✓ validated Debian package: {package_path}")
            elif fmt == "rpm":
                rpm_cmd = get_command("rpm")
                package_path = locate_rpm(
                    package_dir_value, package_value, version_value, release_value
                )
                sandbox_factory = lambda image=rpm_base_image, directory=store_dir: polythene_rootfs(  # noqa: E731
                    polythene_script,
                    image,
                    directory,
                    timeout=timeout_value,
                )
                validate_rpm_package(
                    rpm_cmd,
                    package_path,
                    expected_name=package_value,
                    expected_version=version_value,
                    expected_release=release_value,
                    expected_arch=arch_value,
                    expected_paths=expected_paths_list,
                    executable_paths=executable_paths_list,
                    verify_command=verify_tuple,
                    sandbox_factory=sandbox_factory,
                )
                print(f"✓ validated RPM package: {package_path}")
            else:
                raise ValidationError(f"unsupported package format: {fmt}")


def run() -> None:
    """Entry point for script execution."""

    app()
