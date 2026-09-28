"""Behavioural tests for setup-rust's pinned mold installer.

Each test builds a small stand-in release archive, records its digest in an
injected table, and serves it from a ``file://`` release base URL, so the
script's own download, verification, unpacking, probing and tool-cache logic
runs without the network. The shipped table is exercised only where a test
needs the real pins.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_mold.py"
VERSION = "9.9.9"


def _load_installer() -> ModuleType:
    """Import the installer script by path; the action's scripts are no package."""
    spec = importlib.util.spec_from_file_location("install_mold", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


install_mold = _load_installer()


class Served(typ.NamedTuple):
    """A stand-in release published under a ``file://`` base URL."""

    base_url: str
    archive: Path
    release: object


def _mold_script(reported: str) -> bytes:
    """Return a stand-in ``mold`` that reports *reported* as its version."""
    return f"#!/bin/sh\necho 'mold {reported} (compatible with GNU ld)'\n".encode()


def _add_file(bundle: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    """Add a regular file member to *bundle*."""
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = mode
    bundle.addfile(member, io.BytesIO(data))


def _add_link(bundle: tarfile.TarFile, name: str, target: str) -> None:
    """Add a symbolic-link member to *bundle*."""
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    bundle.addfile(member)


def _write_archive(
    path: Path,
    *,
    reported: str = VERSION,
    with_ld_mold: bool = True,
    extra: tuple[str, bytes] | None = None,
) -> None:
    """Write a release-shaped archive for x86_64 to *path*."""
    root = f"mold-{VERSION}-x86_64-linux"
    with tarfile.open(path, "w:gz") as bundle:
        _add_file(bundle, f"{root}/bin/mold", _mold_script(reported), 0o755)
        if with_ld_mold:
            _add_link(bundle, f"{root}/bin/ld.mold", "mold")
        _add_link(bundle, f"{root}/libexec/mold/ld", "../../bin/mold")
        if extra is not None:
            _add_file(bundle, extra[0], extra[1], 0o644)


def _serve(tmp_path: Path, **archive_options: object) -> Served:
    """Publish a stand-in archive and return its URL, file and pinned release."""
    releases = tmp_path / "releases"
    archive = releases / f"v{VERSION}" / f"mold-{VERSION}-x86_64-linux.tar.gz"
    archive.parent.mkdir(parents=True)
    _write_archive(archive, **archive_options)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    release = install_mold.select_release(VERSION, "X64", {(VERSION, "x86_64"): digest})
    return Served(base_url=releases.as_uri(), archive=archive, release=release)


def _install(tmp_path: Path, served: Served) -> object:
    """Run the installer for *served* into the test's tool cache."""
    return install_mold.install(
        served.release,
        tool_cache=tmp_path / "tool-cache",
        temp_dir=tmp_path / "temp",
        base_url=served.base_url,
    )


def _marker(tmp_path: Path, served: Served) -> Path:
    """Return where the completion marker for *served* would be."""
    tree = install_mold.install_dir(tmp_path / "tool-cache", served.release)
    return tree.parent / f"{tree.name}.complete"


@pytest.mark.parametrize(
    ("runner_arch", "arch"), [("X64", "x86_64"), ("ARM64", "aarch64")]
)
def test_the_default_version_is_pinned_for_both_architectures(
    runner_arch: str, arch: str
) -> None:
    """The version the action defaults to resolves on both Linux architectures."""
    release = install_mold.select_release("2.41.0", runner_arch)

    assert release.arch == arch
    assert release.digest == install_mold.MOLD_DIGESTS[("2.41.0", arch)]
    assert release.url(install_mold.RELEASE_BASE_URL) == (
        "https://github.com/rui314/mold/releases/download/v2.41.0/"
        f"mold-2.41.0-{arch}-linux.tar.gz"
    )


@pytest.mark.parametrize(
    ("version", "runner_arch", "reason"),
    [
        pytest.param("2.41.0", "ARM", "runner architecture 'ARM'", id="arch"),
        pytest.param("2.40.0", "X64", "no SHA-256 is recorded", id="version"),
        pytest.param("latest", "X64", "no SHA-256 is recorded", id="floating"),
    ],
)
def test_an_unpinned_request_is_refused(
    version: str, runner_arch: str, reason: str
) -> None:
    """Only a recorded version on a supported architecture resolves."""
    with pytest.raises(install_mold.MoldInstallError, match=reason):
        install_mold.select_release(version, runner_arch)


def test_the_tool_cache_path_is_keyed_by_digest(tmp_path: Path) -> None:
    """A changed pin for the same version never reuses the old archive's tree."""
    old, new = (
        install_mold.select_release(VERSION, "X64", {(VERSION, "x86_64"): digest})
        for digest in ("a" * 64, "b" * 64)
    )

    assert install_mold.install_dir(tmp_path, old) != install_mold.install_dir(
        tmp_path, new
    )


def test_a_verified_release_is_installed_then_reused(tmp_path: Path) -> None:
    """The first call installs and marks the tree; the second reuses it offline."""
    served = _serve(tmp_path)

    first = _install(tmp_path, served)
    served.archive.unlink()
    second = _install(tmp_path, served)

    assert first.status == "installed"
    assert second.status == "cached"
    assert second.bin_dir == first.bin_dir
    assert (first.bin_dir / "ld.mold").resolve() == (first.bin_dir / "mold").resolve()
    assert _marker(tmp_path, served).is_file()


def test_a_tampered_archive_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    """A digest mismatch fails before anything is unpacked into the tool cache."""
    served = _serve(tmp_path)
    with served.archive.open("ab") as archive:
        archive.write(b"\0")

    with pytest.raises(install_mold.MoldInstallError, match="checksum mismatch"):
        _install(tmp_path, served)

    tree = install_mold.install_dir(tmp_path / "tool-cache", served.release)
    assert not tree.exists()
    assert not _marker(tmp_path, served).exists()


def test_an_archive_without_ld_mold_is_refused(tmp_path: Path) -> None:
    """A compiler driver finds mold as ``ld.mold``, so the tree must provide it."""
    served = _serve(tmp_path, with_ld_mold=False)

    with pytest.raises(install_mold.MoldInstallError, match=r"lacks bin/ld\.mold"):
        _install(tmp_path, served)

    assert not _marker(tmp_path, served).exists()


def test_a_binary_reporting_another_version_is_refused(tmp_path: Path) -> None:
    """The installed binary must report the version that was asked for."""
    served = _serve(tmp_path, reported="1.0.0")

    with pytest.raises(install_mold.MoldInstallError, match=r"reports '1\.0\.0'"):
        _install(tmp_path, served)

    assert not _marker(tmp_path, served).exists()


def test_a_member_escaping_the_archive_is_refused(tmp_path: Path) -> None:
    """A verified archive still cannot write outside its staging directory."""
    served = _serve(tmp_path, extra=("../escaped", b"payload"))

    with pytest.raises(install_mold.MoldInstallError, match="failed to unpack"):
        _install(tmp_path, served)

    assert not list(tmp_path.rglob("escaped"))


def test_a_marked_but_broken_tree_is_reinstalled(tmp_path: Path) -> None:
    """A marker is not trusted on its own: the binary must still run."""
    served = _serve(tmp_path)
    first = _install(tmp_path, served)
    (first.bin_dir / "mold").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    second = _install(tmp_path, served)

    assert second.status == "installed"
    assert install_mold.reported_version(second.bin_dir / "mold") == VERSION


def test_main_reports_the_outcome_through_the_runner_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A success adds mold to PATH and writes the status and version outputs."""
    served = _serve(tmp_path)
    monkeypatch.setattr(
        install_mold, "MOLD_DIGESTS", {(VERSION, "x86_64"): served.release.digest}
    )
    github_path = tmp_path / "github-path"
    github_output = tmp_path / "github-output"

    status = install_mold.main(
        [
            "--mold-version",
            VERSION,
            "--runner-arch",
            "X64",
            "--tool-cache",
            str(tmp_path / "tool-cache"),
            "--temp-dir",
            str(tmp_path / "temp"),
            "--github-path",
            str(github_path),
            "--github-output",
            str(github_output),
            "--release-base-url",
            served.base_url,
        ]
    )

    assert status == 0
    bin_dir = install_mold.install_dir(tmp_path / "tool-cache", served.release) / "bin"
    assert github_path.read_text(encoding="utf-8").splitlines() == [str(bin_dir)]
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "status=installed",
        f"version={VERSION}",
    ]
    assert "metric setup-rust.mold=installed" in capsys.readouterr().out


def test_main_fails_closed_without_touching_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal exits 1, annotates the reason and leaves PATH alone."""
    github_path = tmp_path / "github-path"

    status = install_mold.main(
        [
            "--mold-version",
            "2.41.0",
            "--runner-arch",
            "ARM",
            "--tool-cache",
            str(tmp_path / "tool-cache"),
            "--temp-dir",
            str(tmp_path / "temp"),
            "--github-path",
            str(github_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "::error title=setup-rust mold::" in captured.err
    assert "metric setup-rust.mold=failed" in captured.out
    assert not github_path.exists()


@pytest.mark.parametrize(
    ("banner", "version"),
    [
        pytest.param("mold 2.41.0 (compatible with GNU ld)", "2.41.0", id="mold"),
        pytest.param("GNU ld (GNU Binutils) 2.42", None, id="another-linker"),
        pytest.param("mold", None, id="no-version"),
        pytest.param("", None, id="silent"),
    ],
)
def test_only_a_mold_banner_yields_a_version(banner: str, version: str | None) -> None:
    """A binary that is not mold never passes the version probe."""
    assert install_mold._version_from_banner(banner) == version
