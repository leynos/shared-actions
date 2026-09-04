"""Schema and content tests for the tool manifest.

The manifest is data that decides what CI downloads and runs, so the shape of
every entry is asserted rather than assumed. A malformed digest or a URL that
is not a release archive would otherwise surface as a job failure on whichever
runner happened to reach it first.
"""

from __future__ import annotations

import re
import typing as typ
from urllib.parse import urlparse

import pytest
from _manifest import (
    SUPPORTED_RUNNERS,
    load_tool_manifest,
    manifest_entries,
    manifest_targets,
)
from hypothesis import given, settings
from hypothesis import strategies as st

#: A SHA-256 as this repository records one: 64 lowercase hex characters and
#: nothing else. Mixed case compares unequal against what `sha256sum` prints.
DIGEST = re.compile(r"^[0-9a-f]{64}$")

#: Extensions the action can extract. The manifest cannot name another,
#: because resolution would reject it at the point of use instead.
EXTENSIONS = (".tar.gz", ".tgz", ".zip")

#: Where a release archive may come from. Anything else is either a mirror
#: nobody owns or a host that can change what it serves.
RELEASE_HOSTS = frozenset({"github.com"})

#: Recorded provenance of each pinned digest.
SIDECAR_STATES = frozenset({"match", "absent", "unchecked"})

TRIPLES = frozenset(SUPPORTED_RUNNERS.values())


def _identifiers() -> list[str]:
    return [str(entry["name"]) for entry in manifest_entries()]


class TestSchema:
    """Every entry carries every field, with the right shape."""

    def test_the_schema_version_is_declared(self) -> None:
        """A reader has to know which shape it is looking at."""
        assert load_tool_manifest()["schema"] == 1

    def test_every_tool_has_the_required_fields(self) -> None:
        """A missing field fails at resolution, on one runner, much later."""
        for entry in manifest_entries():
            assert set(entry) == {
                "name",
                "version",
                "binary",
                "version-args",
                "target",
            }, entry.get("name")

    def test_every_target_has_the_required_fields(self) -> None:
        """Same reasoning, for the part that names bytes to download."""
        for entry, target in manifest_targets():
            assert set(target) == {
                "triple",
                "url",
                "sha256",
                "member",
                "sidecar",
            }, f"{entry['name']} {target.get('triple')}"

    @pytest.mark.parametrize("entry", manifest_entries(), ids=_identifiers())
    def test_the_binary_name_is_not_empty(self, entry: dict[str, typ.Any]) -> None:
        """It is what the probe looks for and what the caller runs."""
        assert entry["binary"]

    @pytest.mark.parametrize("entry", manifest_entries(), ids=_identifiers())
    def test_version_args_are_strings(self, entry: dict[str, typ.Any]) -> None:
        """They are joined into a command line, so nothing else will do.

        An empty list is allowed and means the tool cannot be asked, which
        `install-tool` reports as `verify=unsupported` rather than skipping
        quietly.
        """
        assert all(isinstance(argument, str) for argument in entry["version-args"])
        assert all(" " not in argument for argument in entry["version-args"])


class TestDigests:
    """The pinned digest is the trust anchor, so its shape is load-bearing."""

    def test_every_digest_is_lowercase_hex(self) -> None:
        """`sha256sum` prints lowercase; a mixed-case pin never matches."""
        for entry, target in manifest_targets():
            digest = target["sha256"]
            assert DIGEST.match(digest), f"{entry['name']} {target['triple']}: {digest}"

    def test_every_sidecar_state_is_recorded(self) -> None:
        """Whether a pin has a second opinion is data, not an inference."""
        for entry, target in manifest_targets():
            assert target["sidecar"] in SIDECAR_STATES, entry["name"]

    def test_identical_urls_carry_identical_digests(self) -> None:
        """One archive serves both Apple targets, and must hash the same.

        Two entries naming one URL with different digests would mean one of
        them was recorded without downloading, which is the mistake the
        manifest header warns against.
        """
        by_url: dict[str, set[str]] = {}
        for _entry, target in manifest_targets():
            by_url.setdefault(target["url"], set()).add(target["sha256"])

        for url, digests in by_url.items():
            assert len(digests) == 1, f"{url} has {len(digests)} digests"


class TestUrls:
    """A URL decides what is downloaded, so it is checked before it is used."""

    def test_every_url_is_https_to_a_release_host(self) -> None:
        """Plain HTTP or an unowned mirror is somebody else's decision."""
        for entry, target in manifest_targets():
            parsed = urlparse(target["url"])
            assert parsed.scheme == "https", f"{entry['name']}: {target['url']}"
            assert parsed.netloc in RELEASE_HOSTS, f"{entry['name']}: {parsed.netloc}"
            assert "/releases/download/" in parsed.path, target["url"]

    def test_every_url_names_an_extractable_archive(self) -> None:
        """Resolution rejects anything else, so the data cannot contain it."""
        for entry, target in manifest_targets():
            assert target["url"].endswith(EXTENSIONS), (
                f"{entry['name']}: {target['url']}"
            )

    def test_every_windows_member_names_an_exe(self) -> None:
        """A Windows archive that yields an extensionless file is a defect."""
        for entry, target in manifest_targets():
            if "windows" in target["triple"]:
                assert target["member"].endswith(".exe"), entry["name"]


class TestTargets:
    """What is offered, and to whom."""

    def test_every_triple_is_one_the_action_resolves(self) -> None:
        """An entry for a triple no runner maps to could never be reached."""
        for entry, target in manifest_targets():
            assert target["triple"] in TRIPLES, f"{entry['name']}: {target['triple']}"

    def test_no_tool_lists_a_triple_twice(self) -> None:
        """Resolution takes the first match, so a duplicate hides a pin."""
        for entry in manifest_entries():
            triples = [target["triple"] for target in entry["target"]]
            assert len(triples) == len(set(triples)), entry["name"]

    def test_no_tool_and_version_appears_twice(self) -> None:
        """Two entries for one version make the second unreachable."""
        seen = [(entry["name"], entry["version"]) for entry in manifest_entries()]

        assert len(seen) == len(set(seen))

    def test_the_seed_set_is_present(self) -> None:
        """The tools Phase A set out to stop building from source."""
        assert set(_identifiers()) >= {
            "cargo-audit",
            "cargo-nextest",
            "cargo-llvm-cov",
            "cargo-dylint",
            "dylint-link",
            "sccache",
        }

    def test_the_linux_only_tools_are_the_expected_ones(self) -> None:
        """Dylint ships no macOS or Windows archives.

        Recorded as a test so that the day it does, this fails and someone
        adds them, rather than the gap persisting because nobody looked.
        """
        linux_only = {
            entry["name"]
            for entry in manifest_entries()
            if all("linux" in target["triple"] for target in entry["target"])
        }

        assert linux_only == {"cargo-dylint", "dylint-link"}


#: Drawn from the manifest itself: properties below must hold for every entry,
#: and Hypothesis is what stops them holding only for the one an author had in
#: mind when writing the assertion.
TARGET_PAIRS = st.sampled_from(manifest_targets())

_SETTINGS = settings(max_examples=40, derandomize=True, deadline=None)


@given(pair=TARGET_PAIRS)
@_SETTINGS
def test_a_member_path_never_escapes_its_archive(
    pair: tuple[dict[str, typ.Any], dict[str, typ.Any]],
) -> None:
    """The member is joined onto an extraction directory and then read.

    A path that starts at the root or climbs out of it would install
    something from elsewhere on the runner, so no entry may contain one.
    """
    _entry, target = pair
    member = target["member"]

    assert not member.startswith("/")
    assert not member.startswith("\\")
    assert ".." not in member.split("/")
    assert ":" not in member


@given(pair=TARGET_PAIRS)
@_SETTINGS
def test_the_member_ends_with_the_binary_name(
    pair: tuple[dict[str, typ.Any], dict[str, typ.Any]],
) -> None:
    """The installed file is the tool, whatever the archive wraps it in.

    Some upstreams put the binary at the archive root and others under one
    directory; either is fine, but the last component has to be the binary
    the entry claims, or the probe will look for a name nothing installed.
    """
    entry, target = pair
    expected = entry["binary"]
    if "windows" in target["triple"]:
        expected += ".exe"

    assert target["member"].rsplit("/", 1)[-1] == expected


@given(pair=TARGET_PAIRS)
@_SETTINGS
def test_the_url_names_the_version_it_is_pinned_at(
    pair: tuple[dict[str, typ.Any], dict[str, typ.Any]],
) -> None:
    """A URL from a different release is the mistake nobody sees.

    Digests would still verify, because they were computed from whatever the
    URL served, so the only thing standing between a mistyped tag and a
    silently wrong tool is this.
    """
    entry, target = pair

    assert entry["version"] in target["url"], f"{entry['name']}: {target['url']}"
