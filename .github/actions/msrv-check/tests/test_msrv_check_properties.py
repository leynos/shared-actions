"""Property tests for the config removal and the directory walk.

The invariant: whatever depth the working directory sits at below the checkout
root, and whichever of the directories on the path (and off it) hold a cargo
config, removal takes exactly the configs on the path from the starting
directory up to the boundary and nothing else.
"""

from __future__ import annotations

import dataclasses
import itertools
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import msrv_check

_NAMES = st.sampled_from(["a", "b", "c", "d"])
_CONFIGS = st.sampled_from([".cargo/config.toml", ".cargo/config"])


@settings(max_examples=60, deadline=None)
@given(
    depth=st.lists(_NAMES, min_size=0, max_size=4),
    on_path=st.lists(st.tuples(st.integers(0, 4), _CONFIGS), max_size=6),
    off_path=st.lists(st.tuples(_NAMES, _CONFIGS), max_size=3),
)
def test_removal_takes_exactly_the_configs_on_the_path(
    depth: list[str],
    on_path: list[tuple[int, str]],
    off_path: list[tuple[str, str]],
) -> None:
    """Configs from the start up to the boundary go; siblings and ancestors stay."""
    with tempfile.TemporaryDirectory() as scratch:
        outer = Path(scratch).resolve()
        boundary = outer / "checkout"
        chain = [boundary]
        for name in depth:
            chain.append(chain[-1] / name)
        start = chain[-1]
        start.mkdir(parents=True)
        on_path_files = set()
        for index, config in on_path:
            directory = chain[min(index, len(chain) - 1)]
            target = directory / config
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("[build]\n", encoding="utf-8")
            on_path_files.add(target)
        outside = outer / ".cargo" / "config.toml"
        outside.parent.mkdir(parents=True)
        outside.write_text("[build]\n", encoding="utf-8")
        siblings = set()
        for name, config in off_path:
            sibling = boundary / f"other-{name}" / config
            sibling.parent.mkdir(parents=True, exist_ok=True)
            sibling.write_text("[build]\n", encoding="utf-8")
            siblings.add(sibling)

        removed = set(msrv_check.drop_cargo_config(start, boundary))

        assert removed == on_path_files, (removed, on_path_files)
        assert all(not path.exists() for path in on_path_files)
        assert outside.exists(), "a config above the checkout boundary must stay"
        assert all(path.exists() for path in siblings), "siblings must stay"


@settings(max_examples=60, deadline=None)
@given(st.lists(_NAMES, min_size=0, max_size=5))
def test_the_directory_walk_ends_at_the_boundary(depth: list[str]) -> None:
    """The walk lists the start first, the boundary last, one step apart."""
    boundary = Path("/checkout")
    start = boundary.joinpath(*depth)

    walk = msrv_check._directories(start, boundary)

    assert walk[0] == start, walk
    assert walk[-1] == boundary, walk
    assert len(walk) == len(depth) + 1, walk
    assert all(a.parent == b for a, b in itertools.pairwise(walk)), walk


_VERSIONS = st.tuples(st.integers(1, 2), st.integers(0, 99)).map(
    lambda pair: f"{pair[0]}.{pair[1]}"
)


@dataclasses.dataclass(frozen=True)
class Layout:
    """A member crate below a checkout, and where a workspace value may sit."""

    depth: list[str]
    own: str | None
    inherit: bool
    shared: str | None
    shared_level: int


_LAYOUTS = st.builds(
    Layout,
    depth=st.lists(_NAMES, min_size=0, max_size=4),
    own=st.one_of(st.none(), _VERSIONS),
    inherit=st.booleans(),
    shared=st.one_of(st.none(), _VERSIONS),
    shared_level=st.integers(0, 4),
)


def _member_key(layout: Layout) -> str:
    """Return the member's `rust-version` line, or nothing."""
    if layout.own is not None:
        return f'rust-version = "{layout.own}"\n'
    return "rust-version.workspace = true\n" if layout.inherit else ""


def _materialise(layout: Layout, boundary: Path) -> tuple[Path, Path | None]:
    """Write the layout under *boundary*; return the member and workspace manifests."""
    chain = [boundary]
    for name in layout.depth:
        chain.append(chain[-1] / name)
    member = chain[-1]
    member.mkdir(parents=True, exist_ok=True)
    (member / "Cargo.toml").write_text(
        f'[package]\nname = "m"\n{_member_key(layout)}', encoding="utf-8"
    )
    holder = chain[min(layout.shared_level, len(chain) - 1)]
    if holder == member or layout.shared is None:
        return member / "Cargo.toml", None
    (holder / "Cargo.toml").write_text(
        f'[workspace.package]\nrust-version = "{layout.shared}"\n', encoding="utf-8"
    )
    return member / "Cargo.toml", holder / "Cargo.toml"


def _expected_version(layout: Layout, workspace: Path | None) -> str | None:
    """Return what Cargo's rules select: own value, else inherited, else none."""
    if layout.own is not None:
        return layout.own
    return layout.shared if layout.inherit and workspace is not None else None


@settings(max_examples=60, deadline=None)
@given(_LAYOUTS)
def test_version_selection_follows_cargo_inheritance(layout: Layout) -> None:
    """A direct value wins; a workspace value counts only on explicit inheritance."""
    with tempfile.TemporaryDirectory() as scratch:
        boundary = Path(scratch).resolve()
        manifest, workspace = _materialise(layout, boundary)

        try:
            found = msrv_check.read_rust_version(manifest, boundary)
        except msrv_check.MsrvError:
            found = None

        assert found == _expected_version(layout, workspace), (found, layout)
