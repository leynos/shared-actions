"""Utility helpers shared by the packaging integration tests."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import typing as typ
from pathlib import Path, PureWindowsPath

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError, ProcessTimedOut
from syspath_hack import add_to_syspath

from cmd_utils_importer import import_cmd_utils

if typ.TYPE_CHECKING:
    import collections.abc as cabc

run_cmd = import_cmd_utils().run_cmd

TESTS_ROOT = Path(__file__).resolve().parents[1]
add_to_syspath(TESTS_ROOT / "scripts")
from script_utils import unique_match

SCRIPTS_DIR = TESTS_ROOT.parent / "scripts"
add_to_syspath(SCRIPTS_DIR)
import architectures
import package as packaging_script

deb_arch_for_target = architectures.deb_arch_for_target


@dc.dataclass(frozen=True, slots=True)
class PackagingConfig:
    """Static metadata describing the sample packaging project."""

    name: str
    bin_name: str
    version: str
    release: str


Command = tuple[str, ...]
DEFAULT_POLYTHENE_COMMAND: Command = ("polythene",)


def _podman_runtime_available(timeout: float = 5.0) -> bool:
    """Return ``True`` when Podman is installed and ``podman info`` succeeds."""
    podman = shutil.which("podman")
    if podman is None:
        return False

    try:
        result = run_cmd(
            local[podman]["info"],
            method="run",
            timeout=timeout,
        )
    except (OSError, ProcessExecutionError, ProcessTimedOut):
        return False

    return result.returncode == 0


HAS_PODMAN_RUNTIME: typ.Final[bool] = _podman_runtime_available()


@dc.dataclass(frozen=True, slots=True)
class PackagingProject:
    """Resolved filesystem paths required for packaging tests."""

    project_dir: Path
    build_script: Path
    package_script: Path
    polythene_command: Command
    target_root: Path | None = None


@dc.dataclass(frozen=True, slots=True)
class BuildArtefacts:
    """Details about build outputs needed for packaging tests."""

    target: str
    man_page: Path


DEFAULT_TARGET: typ.Final[str] = "x86_64-unknown-linux-gnu"
DEFAULT_CONFIG: typ.Final[PackagingConfig] = PackagingConfig(
    name="rust-toy-app",
    bin_name="rust-toy-app",
    version="0.1.0",
    release="1",
)


def packaging_project() -> PackagingProject:
    """Return the filesystem layout for the packaging fixtures."""
    test_file = Path(__file__).resolve()
    tests_root = test_file.parents[1]
    project_dir = test_file.parents[4] / "rust-toy-app"
    actions_root = tests_root.parent
    return PackagingProject(
        project_dir=project_dir,
        build_script=actions_root / "rust-build-release" / "src" / "main.py",
        package_script=tests_root / "scripts" / "package.py",
        polythene_command=DEFAULT_POLYTHENE_COMMAND,
    )


def _reject_drive_relative_target(inherited: str) -> None:
    """Reject a target whose meaning depends on Windows' per-drive current directory.

    ```python
    _reject_drive_relative_target("build")  # accepted: Cargo-cwd relative
    _reject_drive_relative_target("D:/build")  # accepted: absolute on Windows
    _reject_drive_relative_target("D:build")  # raises ValueError
    ```
    """
    windows_path = PureWindowsPath(inherited)
    if windows_path.drive and not windows_path.root:
        message = f"drive-relative Cargo target is ambiguous: {inherited}"
        raise ValueError(message)


def _effective_target_root(project_dir: Path, inherited: str | None) -> Path:
    """Resolve Cargo's environment path relative to its invocation directory.

    ```python
    _effective_target_root(Path("/work/project"), "../build")
    # Path("/work/project/../build"), the same root Cargo resolves from that cwd
    ```
    """
    if inherited:
        _reject_drive_relative_target(inherited)
        candidate = Path(inherited)
        root = candidate if candidate.is_absolute() else project_dir / candidate
        if root.resolve().is_relative_to(
            Path("/tmp").resolve(),  # noqa: S108 - fixed rejection boundary, no allocation.
        ):
            message = f"integration Cargo target must be outside /tmp: {root}"
            raise ValueError(message)
        return root
    repository = Path(__file__).resolve().parents[4]
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    parent = repository / "target" / "pytest-integration" / worker
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="packaging-", dir=parent))


def _project_target_root(project: PackagingProject) -> Path:
    """Return the root frozen when the integration fixture was cloned.

    ```python
    _project_target_root(cloned_project)  # its frozen external target Path
    _project_target_root(packaging_project())  # raises ValueError: not cloned
    ```
    """
    if project.target_root is None:
        message = "build fixtures must be cloned before resolving their target root"
        raise ValueError(message)
    return project.target_root


def clone_packaging_project(
    tmp_path: Path, project: PackagingProject
) -> PackagingProject:
    """Copy *project* into *tmp_path* and return an updated descriptor."""
    destination = tmp_path / project.project_dir.name
    shutil.copytree(
        project.project_dir,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".cache", ".uv-cache"),
    )
    for leftover in ("target", "dist"):
        stale_path = destination / leftover
        if not stale_path.exists():
            continue
        if stale_path.is_dir():
            shutil.rmtree(stale_path)
        else:
            stale_path.unlink()
    return dc.replace(
        project,
        project_dir=destination,
        target_root=_effective_target_root(
            destination, os.environ.get("CARGO_TARGET_DIR")
        ),
    )


def _resolve_man_page(
    target_root: Path,
    target: str,
    config: PackagingConfig,
) -> Path:
    """Locate the man page for *config.bin_name*.

    Prefers the stable ``target/generated-man/<target>/release/<bin>.1`` path.
    Falls back to the legacy Cargo build-output glob when the stable path is
    absent.  Raises ``FileNotFoundError`` when no match is found and
    ``RuntimeError`` when multiple legacy matches are found.
    """
    stable = target_root / f"generated-man/{target}/release/{config.bin_name}.1"
    if stable.exists():
        return stable
    matches = list(
        target_root.glob(
            f"{target}/release/build/{config.name}-*/out/{config.bin_name}.1"
        )
    )
    if len(matches) == 0:
        message = (
            f"{config.name} man page not found at {stable} "
            f"or under target/{target}/release/build/*/out/"
        )
        raise FileNotFoundError(message)
    if len(matches) > 1:
        paths = "\n  ".join(str(match) for match in matches)
        message = (
            f"expected exactly one legacy man page for {config.name}, "
            f"found {len(matches)}:\n  {paths}"
        )
        raise RuntimeError(message)
    return matches[0]


def _build_child_environment(
    target_root: Path,
    plumbum_environment: cabc.Mapping[str, str],
    process_environment: cabc.Mapping[str, str],
    *,
    container_engine: str | None = None,
) -> dict[str, str]:
    """Preserve effective command controls while routing only this child build.

    ```python
    _build_child_environment(
        Path("/build/shared"), {"PLUMBUM_ONLY": "kept"},
        {"RUSTDOC": "admitted-rustdoc"}, container_engine="podman",
    )
    # Keeps PLUMBUM_ONLY and RUSTDOC, sets CARGO_TARGET_DIR=/build/shared
    # and CROSS_CONTAINER_ENGINE=podman; neither input mapping is mutated.
    ```
    """
    effective = {
        **{key: str(value) for key, value in plumbum_environment.items()},
        **{key: str(value) for key, value in process_environment.items()},
        "CARGO_TARGET_DIR": str(target_root),
    }
    if container_engine is not None:
        effective["CROSS_CONTAINER_ENGINE"] = container_engine
    return effective


def build_release_artefacts(
    project: PackagingProject,
    target: str,
    *,
    config: PackagingConfig = DEFAULT_CONFIG,
) -> BuildArtefacts:
    """Compile the Rust project and return the artefacts needed for packaging."""
    target_root = _project_target_root(project)
    with local.cwd(project.project_dir):
        run_cmd(
            local["rustup"][
                "toolchain",
                "install",
                "1.89.0",
                "--profile",
                "minimal",
                "--no-self-update",
            ]
        )
        run_cmd(
            local[sys.executable][project.build_script.as_posix(), target],
            env=_build_child_environment(
                target_root,
                typ.cast("cabc.Mapping[str, str]", local.env),
                os.environ,
                container_engine="podman",
            ),
        )
        man_src = _resolve_man_page(target_root, target, config)
    return BuildArtefacts(target=target, man_page=man_src)


def package_project(
    project: PackagingProject,
    build: BuildArtefacts,
    *,
    config: PackagingConfig = DEFAULT_CONFIG,
    formats: typ.Iterable[str] = ("deb",),
) -> dict[str, Path]:
    """Package the project with nfpm for the requested formats."""
    # Normalize and deduplicate formats while preserving order.
    ordered_formats: list[str] = []
    for entry in formats:
        trimmed = entry.strip().lower()
        if not trimmed:
            continue
        if trimmed not in ordered_formats:
            ordered_formats.append(trimmed)

    if not ordered_formats:
        return {}

    with local.cwd(project.project_dir):
        dist_dir = project.project_dir / "dist"
        if dist_dir.exists():
            for pattern in (f"{config.name}_*.deb", f"{config.name}-*.rpm"):
                for existing in dist_dir.glob(pattern):
                    existing.unlink()
        with ensure_nfpm(project.project_dir):
            env_vars = {
                "INPUT_PACKAGE_NAME": config.name,
                "INPUT_BIN_NAME": config.bin_name,
                "INPUT_TARGET": build.target,
                "INPUT_VERSION": config.version,
                "INPUT_RELEASE": config.release,
                "INPUT_FORMATS": "\n".join(ordered_formats),
                "INPUT_MAN_PATHS": build.man_page.as_posix(),
                "INPUT_MAN_SECTION": "1",
                "INPUT_BINARY_DIR": str(_project_target_root(project)),
                "INPUT_OUTDIR": "dist",
                "INPUT_CONFIG_PATH": "dist/nfpm.yaml",
            }
            with local.env(**env_vars):
                run_cmd(local["uv"]["run", project.package_script.as_posix()])

        results: dict[str, Path] = {}
        for fmt in ordered_formats:
            if fmt == "deb":
                pattern = f"dist/{config.name}_{config.version}-{config.release}_*.deb"
            elif fmt == "rpm":
                pattern = f"dist/{config.name}-{config.version}-{config.release}*.rpm"
            else:
                continue
            results[fmt] = unique_match(
                project.project_dir.glob(pattern),
                description=f"{config.name} {fmt} package",
            )
    return results


def test_coerce_optional_path_uses_default_for_blank_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank INPUT_BINARY_DIR falls back to the default target directory."""
    monkeypatch.setenv("INPUT_BINARY_DIR", "")
    result = packaging_script._coerce_optional_path(
        Path("target"),
        "INPUT_BINARY_DIR",
        fallback=Path("target"),
    )
    expected = Path("target")
    if result != expected:
        pytest.fail(f"expected {expected} but received {result}")


@dc.dataclass(slots=True)
class PolytheneRootfs:
    """Descriptor for an exported polythene root filesystem."""

    command: Command
    uid: str
    store: Path

    def exec(self, *cmd: str) -> str:
        """Execute ``cmd`` inside the rootfs via polythene."""
        return polythene_exec(self.command, self.uid, self.store.as_posix(), *cmd)

    @property
    def root(self) -> Path:
        """Filesystem path to the exported root."""
        return self.store / self.uid


class IsolationUnavailableError(RuntimeError):
    """Raised when container isolation cannot be established."""

    EMPTY_UID_MESSAGE = "polythene pull returned empty uid"


class ChecksumMismatchError(RuntimeError):
    """Raised when nfpm checksum validation fails."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"nfpm checksum mismatch: expected {expected} but computed {actual}"
        )


class ChecksumDownloadError(RuntimeError):
    """Raised when the nfpm checksum manifest cannot be downloaded."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"failed to download nfpm checksums from {url}")


class ChecksumReadError(RuntimeError):
    """Raised when the nfpm checksum manifest cannot be read from disk."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"failed to read nfpm checksums from {url}")


class ChecksumManifestEntryMissingError(RuntimeError):
    """Raised when the nfpm manifest does not contain the expected asset entry."""

    def __init__(self, url: str, asset: str) -> None:
        self.url = url
        self.asset = asset
        super().__init__(f"nfpm checksum manifest missing entry for {asset} from {url}")


def polythene_cmd(polythene: Command, *args: str) -> str:
    """Execute ``polythene`` with ``uv run`` and return its output."""
    try:
        return run_cmd(local["uv"]["run", *polythene, *args])
    except subprocess.CalledProcessError as exc:
        raise ProcessExecutionError(
            exc.cmd, exc.returncode, exc.output, exc.stderr
        ) from exc


def polythene_exec(polythene: Command, uid: str, store: str, *cmd: str) -> str:
    """Run a command inside the exported rootfs identified by ``uid``."""
    return polythene_cmd(polythene, "exec", uid, "--store", store, "--", *cmd)


@contextlib.contextmanager
def polythene_rootfs(polythene: Command, image: str) -> typ.Iterator[PolytheneRootfs]:
    """Yield an exported rootfs for ``image`` or raise ``IsolationUnavailableError``."""
    with tempfile.TemporaryDirectory() as store:
        try:
            pull_output = polythene_cmd(polythene, "pull", image, "--store", store)
        except (
            ProcessExecutionError,
            subprocess.CalledProcessError,
        ) as exc:  # pragma: no cover - exercised in CI only
            raise IsolationUnavailableError(str(exc)) from exc
        uid = pull_output.splitlines()[-1].strip()
        if not uid:
            raise IsolationUnavailableError(IsolationUnavailableError.EMPTY_UID_MESSAGE)
        try:
            polythene_exec(polythene, uid, store, "true")
        except (
            ProcessExecutionError,
            subprocess.CalledProcessError,
        ) as exc:  # pragma: no cover - exercised in CI only
            raise IsolationUnavailableError(str(exc)) from exc
        yield PolytheneRootfs(command=polythene, uid=uid, store=Path(store))


@contextlib.contextmanager
def ensure_nfpm(project_dir: Path, version: str = "v2.44.1") -> typ.Iterator[Path]:
    """Ensure ``nfpm`` is available on ``PATH`` for the duration of the context."""
    existing = shutil.which("nfpm")
    if existing is not None:
        yield Path(existing)
        return

    # Allow overriding the pinned version in CI environments
    version = os.environ.get("NFPM_VERSION", version)

    host_arch = run_cmd(local["uname"]["-m"]).strip()
    arch_map = {"x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}
    asset_arch = arch_map.get(host_arch, "x86_64")
    host_os = run_cmd(local["uname"]["-s"]).strip()
    os_map = {"Linux": "Linux", "Darwin": "Darwin"}
    asset_os = os_map.get(host_os, "Linux")
    base_url = "https://github.com/goreleaser/nfpm/releases/download/"
    url = f"{base_url}{version}/nfpm_{version[1:]}_{asset_os}_{asset_arch}.tar.gz"
    tools_dir = project_dir / "dist" / ".tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    nfpm_path = tools_dir / "nfpm"
    if not nfpm_path.exists():
        with tempfile.TemporaryDirectory() as td:
            tarball = Path(td) / "nfpm.tgz"
            run_cmd(
                local["curl"][
                    "-fsSL",
                    "--retry",
                    "3",
                    "--retry-connrefused",
                    "--max-time",
                    "120",
                    url,
                    "-o",
                    tarball,
                ]
            )
            checks_url = f"{base_url}{version}/checksums.txt"
            sums_path = Path(td) / "checksums.txt"
            sums_text = ""
            try:
                run_cmd(
                    local["curl"][
                        "-fsSL",
                        "--retry",
                        "3",
                        "--retry-connrefused",
                        "--max-time",
                        "60",
                        checks_url,
                        "-o",
                        sums_path,
                    ]
                )
            except (ProcessExecutionError, subprocess.CalledProcessError) as exc:
                raise ChecksumDownloadError(checks_url) from exc
            try:
                sums_text = sums_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ChecksumReadError(checks_url) from exc

            expected_hash: str | None = None
            pattern = f"nfpm_{version[1:]}_{asset_os}_{asset_arch}.tar.gz"
            for line in sums_text.splitlines():
                if pattern in line:
                    expected_hash = line.split()[0]
                    break
            if expected_hash is None:
                raise ChecksumManifestEntryMissingError(checks_url, pattern)
            if expected_hash:
                digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
                if digest.lower() != expected_hash.lower():
                    raise ChecksumMismatchError(expected_hash, digest)
            run_cmd(local["tar"]["-xzf", tarball, "-C", td, "nfpm"])
            run_cmd(local["install"]["-m", "0755", Path(td) / "nfpm", nfpm_path])

    original_path = os.environ.get("PATH")
    tools = tools_dir.as_posix()
    existing_parts = original_path.split(":") if original_path else []
    filtered_parts = [part for part in existing_parts if part != tools]
    os.environ["PATH"] = ":".join([tools, *filtered_parts]) if filtered_parts else tools
    try:
        yield nfpm_path
    finally:
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path


__all__ = sorted(
    [
        "build_release_artefacts",
        "BuildArtefacts",
        "clone_packaging_project",
        "ChecksumMismatchError",
        "ChecksumDownloadError",
        "ChecksumManifestEntryMissingError",
        "deb_arch_for_target",
        "DEFAULT_CONFIG",
        "DEFAULT_TARGET",
        "HAS_PODMAN_RUNTIME",
        "ensure_nfpm",
        "ChecksumReadError",
        "IsolationUnavailableError",
        "package_project",
        "packaging_project",
        "PackagingConfig",
        "PackagingProject",
        "polythene_cmd",
        "polythene_exec",
        "polythene_rootfs",
        "PolytheneRootfs",
    ],
    key=str.casefold,
)
