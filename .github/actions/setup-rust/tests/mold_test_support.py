"""Stand-in mold releases for setup-rust's installer tests.

Builds release-shaped archives, records their digests in an injected table and
serves them from a ``file://`` base URL, so the installer's own download,
verification, unpacking, probing and tool-cache logic runs without the
network. Shared by the behavioural, boundary and property tests.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_mold.py"
VERSION = "9.9.9"


def load_installer() -> ModuleType:
    """Import the installer script by path; the action's scripts are no package."""
    spec = importlib.util.spec_from_file_location("install_mold", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"cannot load {SCRIPT}"
        raise ImportError(msg)
    # The installer imports its adapters module from its own directory.
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


install_mold = load_installer()


class Served(typ.NamedTuple):
    """A stand-in release published under a ``file://`` base URL."""

    base_url: str
    archive: Path
    release: object


def mold_script(reported: str) -> bytes:
    """Return a stand-in ``mold`` that reports *reported* as its version."""
    return f"#!/bin/sh\necho 'mold {reported} (compatible with GNU ld)'\n".encode()


def add_file(bundle: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    """Add a regular file member to *bundle*."""
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = mode
    bundle.addfile(member, io.BytesIO(data))


def add_link(bundle: tarfile.TarFile, name: str, target: str) -> None:
    """Add a symbolic-link member to *bundle*."""
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    bundle.addfile(member)


def write_archive(
    path: Path,
    *,
    reported: str = VERSION,
    with_ld_mold: bool = True,
    extra: tuple[str, bytes] | None = None,
) -> None:
    """Write a release-shaped archive for x86_64 to *path*."""
    root = f"mold-{VERSION}-x86_64-linux"
    with tarfile.open(path, "w:gz") as bundle:
        add_file(bundle, f"{root}/bin/mold", mold_script(reported), 0o755)
        if with_ld_mold:
            add_link(bundle, f"{root}/bin/ld.mold", "mold")
        add_link(bundle, f"{root}/libexec/mold/ld", "../../bin/mold")
        if extra is not None:
            add_file(bundle, extra[0], extra[1], 0o644)


def serve(tmp_path: Path, **archive_options: object) -> Served:
    """Publish a stand-in archive and return its URL, file and pinned release."""
    releases = tmp_path / "releases"
    archive = releases / f"v{VERSION}" / f"mold-{VERSION}-x86_64-linux.tar.gz"
    archive.parent.mkdir(parents=True)
    write_archive(archive, **archive_options)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    release = install_mold.select_release(VERSION, "X64", {(VERSION, "x86_64"): digest})
    return Served(base_url=releases.as_uri(), archive=archive, release=release)


def install(tmp_path: Path, served: Served) -> object:
    """Run the installer for *served* into the test's tool cache."""
    return install_mold.install(
        served.release,
        tool_cache=tmp_path / "tool-cache",
        temp_dir=tmp_path / "temp",
        adapters=install_mold.Adapters(base_url=served.base_url),
    )


def marker(tmp_path: Path, served: Served) -> Path:
    """Return where the completion marker for *served* would be."""
    tree = install_mold.install_dir(tmp_path / "tool-cache", served.release)
    return tree.parent / f"{tree.name}.complete"
