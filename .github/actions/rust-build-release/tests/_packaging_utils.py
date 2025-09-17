"""Utility helpers shared by the packaging integration tests."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from cmd_utils import run_cmd


@dataclass(slots=True)
class PolytheneRootfs:
    """Descriptor for an exported polythene root filesystem."""

    polythene: Path
    uid: str
    store: Path

    def exec(self, *cmd: str) -> str:
        """Execute ``cmd`` inside the rootfs via polythene."""

        return polythene_exec(self.polythene, self.uid, self.store.as_posix(), *cmd)

    @property
    def root(self) -> Path:
        """Filesystem path to the exported root."""

        return self.store / self.uid


class IsolationUnavailableError(RuntimeError):
    """Raised when container isolation cannot be established."""


def polythene_cmd(polythene: Path, *args: str) -> str:
    """Execute ``polythene`` with ``uv run`` and return its output."""

    return run_cmd(local["uv"]["run", polythene.as_posix(), *args])


def polythene_exec(polythene: Path, uid: str, store: str, *cmd: str) -> str:
    """Run a command inside the exported rootfs identified by ``uid``."""

    return polythene_cmd(polythene, "exec", uid, "--store", store, "--", *cmd)


@contextlib.contextmanager
def polythene_rootfs(polythene: Path, image: str) -> Iterator[PolytheneRootfs]:
    """Yield an exported rootfs for ``image`` or raise ``IsolationUnavailableError``."""

    with tempfile.TemporaryDirectory() as store:
        try:
            pull_output = polythene_cmd(polythene, "pull", image, "--store", store)
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI only
            raise IsolationUnavailableError(str(exc)) from exc
        uid = pull_output.splitlines()[-1].strip()
        try:
            polythene_exec(polythene, uid, store, "true")
        except ProcessExecutionError as exc:  # pragma: no cover - exercised in CI only
            raise IsolationUnavailableError(str(exc)) from exc
        yield PolytheneRootfs(polythene=polythene, uid=uid, store=Path(store))


@contextlib.contextmanager
def ensure_nfpm(project_dir: Path, version: str = "v2.39.0") -> Iterator[Path]:
    """Ensure ``nfpm`` is available on ``PATH`` for the duration of the context."""

    existing = shutil.which("nfpm")
    if existing is not None:
        yield Path(existing)
        return

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
            run_cmd(local["curl"]["-sSL", url, "-o", tarball])
            checks_url = f"{base_url}{version}/nfpm_{version[1:]}_checksums.txt"
            try:
                sums_path = Path(td) / "checksums.txt"
                run_cmd(local["curl"]["-sSL", checks_url, "-o", sums_path])
                expected_hash = None
                pattern = f"nfpm_{version[1:]}_{asset_os}_{asset_arch}.tar.gz"
                for line in sums_path.read_text(encoding="utf-8").splitlines():
                    if pattern in line:
                        expected_hash = line.split()[0]
                        break
                if expected_hash:
                    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
                    if digest.lower() != expected_hash.lower():
                        raise RuntimeError(
                            "nfpm checksum mismatch: expected"
                            f" {expected_hash} but computed {digest}"
                        )
            except Exception:
                pass  # Best-effort integrity verification
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


__all__ = [
    "IsolationUnavailableError",
    "PolytheneRootfs",
    "ensure_nfpm",
    "polythene_cmd",
    "polythene_exec",
    "polythene_rootfs",
]
