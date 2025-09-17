"""Utility helpers shared by the packaging integration tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from plumbum import local

from cmd_utils import run_cmd


def polythene_cmd(polythene: Path, *args: str) -> str:
    """Execute ``polythene`` with ``uv run`` and return its output."""

    return run_cmd(local["uv"]["run", polythene.as_posix(), *args])


def polythene_exec(polythene: Path, uid: str, store: str, *cmd: str) -> str:
    """Run a command inside the exported rootfs identified by ``uid``."""

    return polythene_cmd(polythene, "exec", uid, "--store", store, "--", *cmd)


def ensure_nfpm(project_dir: Path, version: str = "v2.39.0") -> None:
    """Download and place ``nfpm`` on ``PATH`` if it is not already available."""

    if shutil.which("nfpm") is not None:
        return

    host = run_cmd(local["uname"]["-m"]).strip()
    arch_map = {"x86_64": "x86_64", "aarch64": "arm64"}
    asset_arch = arch_map.get(host, "x86_64")
    base_url = "https://github.com/goreleaser/nfpm/releases/download/"
    url = f"{base_url}{version}/nfpm_{version[1:]}_Linux_{asset_arch}.tar.gz"
    tools_dir = project_dir / "dist" / ".tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tarball = Path(td) / "nfpm.tgz"
        run_cmd(local["curl"]["-sSL", url, "-o", tarball])
        run_cmd(local["tar"]["-xzf", tarball, "-C", td, "nfpm"])
        run_cmd(local["install"]["-m", "0755", Path(td) / "nfpm", tools_dir / "nfpm"])

    path = os.environ.get("PATH", "")
    prefix = f"{tools_dir.as_posix()}:"
    if not path.startswith(prefix):
        os.environ["PATH"] = f"{prefix}{path}"


__all__ = ["ensure_nfpm", "polythene_cmd", "polythene_exec"]
