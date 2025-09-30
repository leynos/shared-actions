#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
#   "plumbum>=1.8,<2.0",
# ]
# ///

"""Validate Linux packages by inspecting metadata and running sandboxed installs."""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import re
import sys
import tempfile
import typing as typ
from dataclasses import dataclass
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from plumbum import local
from plumbum.commands.base import BaseCommand
from plumbum.commands.processes import ProcessExecutionError

SIBLING_SCRIPTS = Path(__file__).resolve().parents[2] / "linux-packages" / "scripts"
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.append(str(SIBLING_SCRIPTS))

from script_utils import ensure_directory, ensure_exists, get_command, run_cmd, unique_match
from architectures import UnsupportedTargetError, deb_arch_for_target, nfpm_arch_for_target


__all__ = [
    "app",
    "main",
    "run",
    "ValidationError",
    "inspect_deb_package",
    "inspect_rpm_package",
]


class ValidationError(RuntimeError):
    """Raised when package validation fails."""


@dataclass(slots=True)
class DebMetadata:
    """Metadata extracted from a Debian package."""

    name: str
    version: str
    architecture: str
    files: set[str]


@dataclass(slots=True)
class RpmMetadata:
    """Metadata extracted from an RPM package."""

    name: str
    version: str
    release: str
    architecture: str
    files: set[str]


@dataclass(slots=True)
class PolytheneSession:
    """Handle for executing commands inside an exported polythene rootfs."""

    script: Path
    uid: str
    store: Path
    timeout: int | None = None

    @property
    def root(self) -> Path:
        """Return the root filesystem path for this session."""

        return self.store / self.uid

    def exec(self, *args: str, timeout: int | None = None) -> str:
        """Execute ``args`` inside the sandbox and return its stdout."""

        effective_timeout = timeout if timeout is not None else self.timeout
        cmd = local[
            "uv"
        ][
            "run",
            self.script.as_posix(),
            "exec",
            self.uid,
            "--store",
            self.store.as_posix(),
            "--",
            *args,
        ]
        return _run_text(cmd, timeout=effective_timeout)


app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
app.config = (*tuple(existing_config), _env_config)


def _run_text(command: BaseCommand, *, timeout: int | None = None) -> str:
    """Execute ``command`` and return its stdout as ``str``."""

    result = run_cmd(command, timeout=timeout)
    if isinstance(result, tuple):
        return "".join(str(part) for part in result if part is not None)
    if isinstance(result, int):
        return ""
    return str(result)


def _parse_kv_output(text: str) -> dict[str, str]:
    """Return ``key: value`` lines from ``text`` as a dictionary."""

    entries: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        entries[key.strip()] = value.strip()
    return entries


def _parse_dpkg_listing(output: str) -> set[str]:
    """Return payload paths from ``dpkg-deb -c`` output."""

    files: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=5)
        path = parts[-1] if parts else ""
        if not path:
            continue
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/"):
            files.add(path)
        else:
            files.add(f"/{path}")
    return files


def inspect_deb_package(dpkg_deb: BaseCommand, package_path: Path) -> DebMetadata:
    """Return metadata for ``package_path`` using ``dpkg-deb``."""

    info_output = _run_text(
        dpkg_deb[
            "-f",
            package_path.as_posix(),
            "Package",
            "Version",
            "Architecture",
        ]
    )
    info = _parse_kv_output(info_output)
    listing_output = _run_text(dpkg_deb["-c", package_path.as_posix()])
    return DebMetadata(
        name=info.get("Package", ""),
        version=info.get("Version", ""),
        architecture=info.get("Architecture", ""),
        files=_parse_dpkg_listing(listing_output),
    )


def _parse_rpm_listing(output: str) -> set[str]:
    """Return payload paths from ``rpm -qlp`` output."""

    files: set[str] = set()
    for line in output.splitlines():
        entry = line.strip()
        if entry:
            files.add(entry)
    return files


def inspect_rpm_package(rpm_cmd: BaseCommand, package_path: Path) -> RpmMetadata:
    """Return metadata for ``package_path`` using ``rpm``."""

    info_output = _run_text(rpm_cmd["-qip", package_path.as_posix()])
    info = _parse_kv_output(info_output)
    listing_output = _run_text(rpm_cmd["-qlp", package_path.as_posix()])
    return RpmMetadata(
        name=info.get("Name", ""),
        version=info.get("Version", ""),
        release=info.get("Release", ""),
        architecture=info.get("Architecture", ""),
        files=_parse_rpm_listing(listing_output),
    )


def _default_polythene_path() -> Path:
    """Return the default path to the polythene helper script."""

    return SIBLING_SCRIPTS / "polythene.py"


def polythene_rootfs(
    polythene: Path,
    image: str,
    store: Path,
    *,
    timeout: int | None = None,
) -> cabc.Iterator[PolytheneSession]:
    """Yield a :class:`PolytheneSession` for ``image`` using ``store``."""

    ensure_directory(store)
    pull_cmd = local[
        "uv"
    ][
        "run",
        polythene.as_posix(),
        "pull",
        image,
        "--store",
        store.as_posix(),
    ]
    try:
        pull_output = _run_text(pull_cmd, timeout=timeout)
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        raise ValidationError(f"polythene pull failed: {exc}") from exc
    uid = pull_output.splitlines()[-1].strip()
    if not uid:
        raise ValidationError("polythene pull returned an empty identifier")
    session = PolytheneSession(polythene, uid, store, timeout)
    ensure_directory(session.root, exist_ok=True)
    try:
        session.exec("true")
    except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
        raise ValidationError(f"polythene exec failed: {exc}") from exc
    try:
        yield session
    finally:
        pass


def _normalise_formats(values: list[str] | None) -> list[str]:
    """Return ordered, deduplicated, lower-cased formats."""

    if not values:
        return ["deb"]
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in values:
        for token in re.split(r"[\s,]+", entry.strip()):
            if not token:
                continue
            lowered = token.lower()
            if lowered not in seen:
                seen.add(lowered)
                ordered.append(lowered)
    return ordered


def _normalise_paths(values: list[str] | None) -> list[str]:
    """Return absolute paths derived from ``values`` while preserving order."""

    if not values:
        return []
    paths: list[str] = []
    for entry in values:
        for line in entry.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if not cleaned.startswith("/"):
                raise ValidationError(f"expected absolute path but received {cleaned!r}")
            paths.append(cleaned)
    return _dedupe(paths)


def _normalise_command(value: list[str] | None) -> list[str]:
    """Return a cleaned command vector."""

    if not value:
        return []
    return [part for part in (item.strip() for item in value) if part]


def _dedupe(values: cabc.Iterable[str]) -> list[str]:
    """Return ``values`` without duplicates while preserving order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _ensure_subset(expected: cabc.Collection[str], actual: cabc.Collection[str], label: str) -> None:
    """Raise :class:`ValidationError` when ``expected`` is not contained in ``actual``."""

    missing = [path for path in expected if path not in actual]
    if missing:
        raise ValidationError(f"missing {label}: {', '.join(missing)}")


def _acceptable_rpm_architectures(arch: str) -> set[str]:
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


def _locate_deb(
    package_dir: Path,
    package_name: str,
    version: str,
    release: str,
) -> Path:
    pattern = f"{package_name}_{version}-{release}_*.deb"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} deb package"
    )


def _locate_rpm(
    package_dir: Path,
    package_name: str,
    version: str,
    release: str,
) -> Path:
    pattern = f"{package_name}-{version}-{release}*.rpm"
    return unique_match(
        package_dir.glob(pattern), description=f"{package_name} rpm package"
    )


def _validate_deb_package(
    dpkg_deb: BaseCommand,
    package_path: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_deb_version: str,
    expected_arch: str,
    expected_paths: cabc.Collection[str],
    executable_paths: cabc.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: cabc.Callable[[], cabc.ContextManager[PolytheneSession]],
) -> None:
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
    _ensure_subset(expected_paths, metadata.files, "Debian package payload")

    with sandbox_factory() as sandbox:
        dest = sandbox.root / package_path.name
        ensure_directory(dest.parent)
        dest.write_bytes(package_path.read_bytes())
        try:
            sandbox.exec("dpkg", "-i", package_path.name)
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
            raise ValidationError(f"dpkg installation failed: {exc}") from exc
        for path in expected_paths:
            sandbox.exec("test", "-e", path)
        for path in executable_paths:
            sandbox.exec("test", "-x", path)
        if verify_command:
            sandbox.exec(*verify_command)
        with contextlib.suppress(ProcessExecutionError):
            sandbox.exec("dpkg", "-r", expected_name)


def _validate_rpm_package(
    rpm_cmd: BaseCommand,
    package_path: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_release: str,
    expected_arch: str,
    expected_paths: cabc.Collection[str],
    executable_paths: cabc.Collection[str],
    verify_command: tuple[str, ...],
    sandbox_factory: cabc.Callable[[], cabc.ContextManager[PolytheneSession]],
) -> None:
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
    acceptable_arches = _acceptable_rpm_architectures(expected_arch)
    if metadata.architecture not in acceptable_arches:
        raise ValidationError(
            "unexpected rpm architecture: expected one of "
            f"{sorted(acceptable_arches)!r}, found {metadata.architecture!r}"
        )
    _ensure_subset(expected_paths, metadata.files, "RPM package payload")

    with sandbox_factory() as sandbox:
        dest = sandbox.root / package_path.name
        ensure_directory(dest.parent)
        dest.write_bytes(package_path.read_bytes())
        try:
            sandbox.exec("rpm", "-i", "--nodeps", "--nosignature", package_path.name)
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI
            raise ValidationError(f"rpm installation failed: {exc}") from exc
        for path in expected_paths:
            sandbox.exec("test", "-e", path)
        for path in executable_paths:
            sandbox.exec("test", "-x", path)
        if verify_command:
            sandbox.exec(*verify_command)
        with contextlib.suppress(ProcessExecutionError):
            sandbox.exec("rpm", "-e", expected_name)


@app.default
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

    format_list = _normalise_formats(formats)
    if not format_list:
        raise ValidationError("no package formats provided")

    expected_paths_list = _normalise_paths(expected_paths)
    default_binary_path = f"/usr/bin/{bin_value}"
    if not expected_paths_list:
        expected_paths_list = [default_binary_path]
    elif default_binary_path not in expected_paths_list:
        expected_paths_list.insert(0, default_binary_path)

    executable_paths_list = _normalise_paths(executable_paths)
    if not executable_paths_list:
        executable_paths_list = [default_binary_path]
    else:
        for entry in executable_paths_list:
            if entry not in expected_paths_list:
                expected_paths_list.append(entry)

    verify_tuple = tuple(_normalise_command(verify_command))

    polythene_script = polythene_path or _default_polythene_path()
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
                package_path = _locate_deb(
                    package_dir_value, package_value, version_value, release_value
                )
                sandbox_factory = lambda image=deb_base_image, directory=store_dir: polythene_rootfs(  # noqa: E731
                    polythene_script,
                    image,
                    directory,
                    timeout=timeout_value,
                )
                _validate_deb_package(
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
                package_path = _locate_rpm(
                    package_dir_value, package_value, version_value, release_value
                )
                sandbox_factory = lambda image=rpm_base_image, directory=store_dir: polythene_rootfs(  # noqa: E731
                    polythene_script,
                    image,
                    directory,
                    timeout=timeout_value,
                )
                _validate_rpm_package(
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


if __name__ == "__main__":  # pragma: no cover - script execution
    run()
