"""Property tests for the mold installer's archive containment.

``unpack`` promises that a verified archive still cannot write outside its
staging directory, whatever its members are named or point at. These
properties generate the unsafe shapes (absolute names, parent traversal at any
depth, and symbolic or hard links whose targets leave the staging directory),
add each to an otherwise valid release, and require the unpack to be refused
with nothing written outside the staging directory. An absolute name is
stripped to a relative one by the ``data`` filter, so it lands inside staging
and is refused as an entry outside the release's top-level directory.
"""

from __future__ import annotations

import io
import tarfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st
from mold_test_support import VERSION, add_file, add_link, install_mold, mold_script

if typ.TYPE_CHECKING:
    from pathlib import Path

ROOT = f"mold-{VERSION}-x86_64-linux"
#: Each example writes and unpacks an archive, so its duration measures the
#: host's filesystem, not the code (#487): no deadline.
FILESYSTEM_PROPERTY = settings(max_examples=60, deadline=None)

SEGMENTS = st.text(alphabet="abcxyz", min_size=1, max_size=6)


def _climbing(minimum: int) -> st.SearchStrategy[str]:
    """Return relative paths that climb at least *minimum* directories."""
    return st.builds(
        lambda depth, tail: "/".join([".."] * depth + tail),
        st.integers(min_value=minimum, max_value=minimum + 4),
        st.lists(SEGMENTS, min_size=1, max_size=3),
    )


#: A member name climbing one level already leaves the staging directory.
ESCAPING_NAME = _climbing(1)
#: A link at ``<root>/share/escape`` needs three levels to leave staging:
#: ``..`` is the root, ``../..`` is staging itself.
ESCAPING_TARGET = _climbing(3)
ABSOLUTE = st.lists(SEGMENTS, min_size=1, max_size=3).map(
    lambda parts: "/" + "/".join(parts)
)


class Unsafe(typ.NamedTuple):
    """One unsafe member: its kind, its name, and a link target when a link."""

    kind: typ.Literal["file", "symlink", "hardlink"]
    name: str
    target: str


UNSAFE_MEMBERS = st.one_of(
    st.builds(Unsafe, st.just("file"), ESCAPING_NAME, st.just("")),
    st.builds(Unsafe, st.just("file"), ABSOLUTE, st.just("")),
    st.builds(
        Unsafe,
        st.just("symlink"),
        st.just(f"{ROOT}/share/escape"),
        st.one_of(ESCAPING_TARGET, ABSOLUTE),
    ),
    st.builds(
        Unsafe,
        st.just("hardlink"),
        st.just(f"{ROOT}/share/escape"),
        st.one_of(ESCAPING_TARGET, ABSOLUTE),
    ),
)


def _archive_with(path: Path, unsafe: Unsafe) -> None:
    """Write a valid release archive to *path* with *unsafe* added last."""
    with tarfile.open(path, "w:gz") as bundle:
        add_file(bundle, f"{ROOT}/bin/mold", mold_script(VERSION), 0o755)
        add_link(bundle, f"{ROOT}/bin/ld.mold", "mold")
        if unsafe.kind == "file":
            add_file(bundle, unsafe.name, b"payload", 0o644)
        elif unsafe.kind == "symlink":
            add_link(bundle, unsafe.name, unsafe.target)
        else:
            member = tarfile.TarInfo(unsafe.name)
            member.type = tarfile.LNKTYPE
            member.linkname = unsafe.target
            bundle.addfile(member, io.BytesIO(b""))


@FILESYSTEM_PROPERTY
@given(unsafe=UNSAFE_MEMBERS)
def test_no_member_can_leave_the_staging_directory(
    tmp_path_factory: object, unsafe: Unsafe
) -> None:
    """Every unsafe member is refused, and nothing lands beside the staging tree."""
    workspace = tmp_path_factory.mktemp("unpack")
    archive = workspace / "release.tar.gz"
    _archive_with(archive, unsafe)
    staging = workspace / "deep" / "er" / "staging"
    staging.mkdir(parents=True)
    release = install_mold.Release(version=VERSION, arch="x86_64", digest="0" * 64)

    try:
        install_mold.unpack(archive, release, staging)
    except install_mold.ArchiveError:
        refused = True
    else:
        refused = False

    outside = sorted(
        path.relative_to(workspace)
        for path in workspace.rglob("*")
        if not path.is_relative_to(staging) and path != archive
    )
    assert refused, unsafe
    assert [str(path) for path in outside] == ["deep", "deep/er"]
