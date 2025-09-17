from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_cmd
from _packaging_utils import (
    IsolationUnavailableError,
    ensure_nfpm,
    polythene_rootfs,
)

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
from script_utils import unique_match  # noqa: E402

RPM_TARGET = "x86_64-unknown-linux-gnu"
RPM_BASE_IMAGE = "docker.io/library/rockylinux:9"


def _parse_rpm_info(output: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key.strip()] = value.strip()
    return info


@dataclass(frozen=True)
class PackagingPaths:
    """Resolved filesystem paths required for packaging tests."""

    project_dir: Path
    build_script: Path
    package_script: Path
    polythene_script: Path


@pytest.fixture(scope="module")
def packaging_paths() -> PackagingPaths:
    """Return the filesystem layout for the packaging fixtures."""

    test_file = Path(__file__).resolve()
    tests_root = test_file.parents[1]
    project_dir = test_file.parents[4] / "rust-toy-app"
    return PackagingPaths(
        project_dir=project_dir,
        build_script=tests_root / "src" / "main.py",
        package_script=tests_root / "scripts" / "package.py",
        polythene_script=tests_root / "scripts" / "polythene.py",
    )


@pytest.fixture(scope="module")
def built_artifacts(packaging_paths: PackagingPaths) -> tuple[str, Path]:
    """Compile the Rust project and return the build target and man page path."""

    target = RPM_TARGET
    project_dir = packaging_paths.project_dir
    with local.cwd(project_dir):
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
        with local.env(CROSS_CONTAINER_ENGINE="podman"):
            run_cmd(local[sys.executable][packaging_paths.build_script.as_posix(), target])
        man_src = unique_match(
            project_dir.glob(
                f"target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
            ),
            description="rust-toy-app man page",
        )
    return target, man_src


@pytest.fixture
def rpm_package_path(
    packaging_paths: PackagingPaths, built_artifacts: tuple[str, Path]
) -> Path:
    """Package the project as an RPM and return the artifact path."""

    target, man_src = built_artifacts
    project_dir = packaging_paths.project_dir
    with local.cwd(project_dir):
        dist_dir = project_dir / "dist"
        if dist_dir.exists():
            for existing in dist_dir.glob("rust-toy-app-0.1.0-1*.rpm"):
                existing.unlink()
        with ensure_nfpm(project_dir):
            run_cmd(
                local["uv"][
                    "run",
                    packaging_paths.package_script.as_posix(),
                    "--name",
                    "rust-toy-app",
                    "--bin-name",
                    "rust-toy-app",
                    "--target",
                    target,
                    "--version",
                    "0.1.0",
                    "--formats",
                    "rpm",
                    "--man",
                    man_src.as_posix(),
                ]
            )
        rpm_path = unique_match(
            project_dir.glob("dist/rust-toy-app-0.1.0-1*.rpm"),
            description="rust-toy-app rpm",
        )
    return rpm_path


@pytest.mark.usefixtures("uncapture_if_verbose")
@pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("podman") is None
    or shutil.which("uv") is None,
    reason="podman or uv not available",
)
def test_rpm_package_metadata(
    packaging_paths: PackagingPaths, rpm_package_path: Path
) -> None:
    """Build the .rpm package and inspect its metadata inside an isolated rootfs."""
    try:
        with polythene_rootfs(
            packaging_paths.polythene_script, RPM_BASE_IMAGE
        ) as rootfs:
            shutil.copy(rpm_package_path, rootfs.root / rpm_package_path.name)
            info_output = rootfs.exec("rpm", "-qip", rpm_package_path.name)
            info = _parse_rpm_info(info_output)
            assert info.get("Name") == "rust-toy-app"
            assert info.get("Version") == "0.1.0"
            release_value = info.get("Release", "")
            assert release_value.startswith("1"), release_value
            arch_value = info.get("Architecture")
            assert arch_value in {"amd64", "x86_64", "arm64", "aarch64"}, arch_value

            listing_output = rootfs.exec("rpm", "-qlp", rpm_package_path.name)
            listing = {line.strip() for line in listing_output.splitlines() if line.strip()}
            assert "/usr/bin/rust-toy-app" in listing
            assert "/usr/share/man/man1/rust-toy-app.1.gz" in listing
    except IsolationUnavailableError as exc:
        pytest.skip(f"isolation unavailable: {exc}")
