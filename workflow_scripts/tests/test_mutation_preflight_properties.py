"""Property-based tests for the mutation preflight's pure rules.

The bounded capture, the version matcher and the sandbox path rules are all
small functions with a contract that holds for every input, not only for the
examples the phase tests happen to use. These cases state those contracts.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts.mutationpreflight.constants import (
    EXCLUDED_DIRECTORIES,
    LOG_BOUND_BYTES,
)
from workflow_scripts.mutationpreflight.running import (
    TRUNCATION_PREFIX,
    bounded,
    reports_version,
)
from workflow_scripts.mutationpreflight.sandbox import _covers, path_problem

#: One path segment: a leading letter, then characters a path may carry.
SEGMENT = st.from_regex(r"[a-z][a-z0-9_.-]{0,7}", fullmatch=True)

#: A relative path, as a declaration would spell it.
PATHS = st.lists(SEGMENT, min_size=1, max_size=4)

#: A stable release, with at least two dotted numeric components.
STABLE = st.from_regex(r"\d{1,3}(?:\.\d{1,3}){1,3}", fullmatch=True)


@given(text=st.text(max_size=2 * LOG_BOUND_BYTES))
def test_bounded_keeps_a_prefix_and_counts_every_dropped_byte(text: str) -> None:
    """A stream is either kept whole or cut at the bound with an exact count."""
    encoded = text.encode("utf-8", "replace")

    kept = bounded(text)

    if len(encoded) <= LOG_BOUND_BYTES:
        assert kept == text, "a stream inside the bound must be kept whole"
        return
    prefix = encoded[:LOG_BOUND_BYTES].decode("utf-8", "ignore")
    dropped = len(encoded) - LOG_BOUND_BYTES
    assert kept == f"{prefix}\n{TRUNCATION_PREFIX}{dropped} bytes truncated]", (
        "a stream over the bound must keep the first bound's worth of bytes and "
        "name exactly how many were dropped"
    )


@given(version=STABLE)
def test_a_stable_version_reports_itself_and_its_dotted_descendants(
    version: str,
) -> None:
    """A declared release matches the release itself and a more precise one."""
    assert reports_version(f"tool {version}", version), f"{version} must report itself"
    assert reports_version(f"tool {version}.4", version), (
        f"a dotted descendant of {version} must satisfy a declaration of {version}"
    )


@given(version=STABLE)
def test_a_pre_release_suffix_is_a_different_release(version: str) -> None:
    """A suffix makes the token a different release from the stable version."""
    assert not reports_version(f"tool {version}rc1", version), (
        "a release candidate must not satisfy a declaration of the stable version"
    )
    assert not reports_version(f"tool {version}rc1.dev1", f"{version}rc1"), (
        "a development build of a candidate is another release again"
    )


@given(wanted=STABLE, published=STABLE)
def test_version_matching_is_equality_or_a_dotted_prefix(
    wanted: str, published: str
) -> None:
    """A stable token matches exactly when it equals or extends the wanted one."""
    expected = published == wanted or published.startswith(f"{wanted}.")

    assert reports_version(f"tool {published}", wanted) is expected, (
        f"{published!r} must match {wanted!r} only as an equal or a dotted "
        f"descendant, not as a prefix of a longer component"
    )


@given(version=STABLE)
def test_a_longer_component_is_not_a_nearby_version(version: str) -> None:
    """Appending a digit makes a different component, not a more precise one."""
    assert not reports_version(f"tool {version}0", version), (
        f"{version}0 is not {version}, however much it resembles it"
    )


@given(parts=PATHS)
def test_a_relative_path_outside_build_output_is_usable(parts: list[str]) -> None:
    """A path is refused by its own parts, never by its position in the tree."""
    declared = "/".join(parts)

    problem = path_problem(declared)

    if any(part in EXCLUDED_DIRECTORIES for part in parts):
        assert problem is not None, (
            "a path under build output or a cache must be refused"
        )
        return
    assert problem is None, f"{declared} must be usable, but: {problem}"


@given(parts=PATHS, depth=st.integers(min_value=1, max_value=3))
def test_a_parent_escape_is_refused(parts: list[str], depth: int) -> None:
    """A path climbing out of the checkout is refused wherever it starts."""
    declared = "/".join([*parts, *([".."] * depth), "file.txt"])

    assert path_problem(declared) == "escapes the checkout", declared


@given(parts=PATHS)
def test_an_absolute_path_is_refused(parts: list[str]) -> None:
    """A sandbox path names a place inside the checkout, never an absolute one."""
    declared = "/" + "/".join(parts)

    assert path_problem(declared) == (
        "is absolute, but sandbox paths are relative to the checkout"
    ), declared


@given(parts=PATHS)
def test_covers_is_reflexive_and_reaches_descendants(parts: list[str]) -> None:
    """A copy covers itself and everything below it, and no sibling."""
    entry = "/".join(parts)

    assert _covers(entry, entry), "a copy must cover the path it names"
    assert _covers(entry, f"{entry}/child"), (
        "a copied directory must cover what it contains"
    )
    assert not _covers(entry, f"{entry}child"), (
        "a copy must not cover a path that merely begins with its name"
    )
