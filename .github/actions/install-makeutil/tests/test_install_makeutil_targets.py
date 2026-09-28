"""Target resolution, digest lookup, and input validation.

These are the pure decisions the `resolve` subcommand makes before anything
is downloaded: which target triple a runner maps to, which digest a version
is pinned to, and whether the caller's `version`/`bin-dir` inputs are even
well-formed.
"""

from __future__ import annotations

import typing as typ

import pytest
from install_makeutil import (
    build_asset_urls,
    build_cache_key,
    lookup_digest,
    resolve_bin_dir,
    resolve_target,
    validate_sha256_override,
    validate_version,
)
from makeutil_errors import (
    InvalidInputError,
    UnknownVersionError,
    UnsupportedPlatformError,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_SUPPORTED = {
    ("Linux", "X64"): "x86_64-unknown-linux-musl",
    ("Linux", "ARM64"): "aarch64-unknown-linux-musl",
}


class TestResolveTarget:
    """Runner OS/architecture pairs map to makeutil's published targets."""

    @pytest.mark.parametrize(("runner_os", "runner_arch"), list(_SUPPORTED))
    def test_a_supported_pair_resolves_to_its_target(
        self, runner_os: str, runner_arch: str
    ) -> None:
        """Both platforms this action installs for resolve without error."""
        assert (
            resolve_target(runner_os, runner_arch)
            == _SUPPORTED[(runner_os, runner_arch)]
        )

    @pytest.mark.parametrize(
        ("runner_os", "runner_arch"),
        [
            ("Windows", "X64"),
            ("macOS", "ARM64"),
            ("Linux", "X86"),
            ("", ""),
        ],
    )
    def test_an_unsupported_pair_is_refused(
        self, runner_os: str, runner_arch: str
    ) -> None:
        """Every other pair fails closed and names the supported platforms."""
        with pytest.raises(UnsupportedPlatformError) as excinfo:
            resolve_target(runner_os, runner_arch)

        assert "Linux/X64" in str(excinfo.value)
        assert "Linux/ARM64" in str(excinfo.value)


class TestLookupDigest:
    """The pinned digest table, and its refusal of an unknown version."""

    def test_the_pinned_0_1_0_digests_are_returned(self) -> None:
        """The two digests the packet specifies are exactly what is pinned."""
        assert (
            lookup_digest("0.1.0", "x86_64-unknown-linux-musl")
            == "99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d"
        )
        assert (
            lookup_digest("0.1.0", "aarch64-unknown-linux-musl")
            == "72b1513eb8640ee18ec0298cb9c0209985a339826863e24e186ceb0929a1c05b"
        )

    def test_an_unpinned_version_is_refused(self) -> None:
        """A version absent from the table fails rather than installing
        unverified; the error names what must change to unblock it.
        """
        with pytest.raises(UnknownVersionError) as excinfo:
            lookup_digest("0.0.1", "x86_64-unknown-linux-musl")

        assert "0.0.1" in str(excinfo.value)
        assert "digest table" in str(excinfo.value)


class TestAssetUrls:
    """The asset and sidecar URLs the action derives from the table."""

    def test_the_sidecar_url_is_the_binary_url_with_sha256_appended(self) -> None:
        """The sidecar is fetched from the same asset URL, plus one suffix."""
        binary_url, sidecar_url = build_asset_urls("0.1.0", "x86_64-unknown-linux-musl")

        assert binary_url == (
            "https://github.com/leynos/makeutil/releases/download/"
            "v0.1.0/makeutil-x86_64-unknown-linux-musl"
        )
        assert sidecar_url == f"{binary_url}.sha256"


class TestCacheKey:
    """The cache key folds in the version, the target, and the digest."""

    def test_the_cache_key_has_every_component(self) -> None:
        """Changing any one of the three inputs must change the key."""
        key = build_cache_key("0.1.0", "x86_64-unknown-linux-musl", "abc123")

        assert key == "install-makeutil-0.1.0-x86_64-unknown-linux-musl-abc123"


class TestValidateVersion:
    """The version grammar: three numeric components, no leading zeros."""

    @pytest.mark.parametrize("version", ["0.1.0", "1.2.3", "10.20.30"])
    def test_a_well_formed_version_is_accepted(self, version: str) -> None:
        """No exception is the whole assertion; validation is a pure check."""
        validate_version(version)

    @pytest.mark.parametrize(
        "version", ["01.1.0", "0.1", "0.1.0.1", "v0.1.0", "", "0.1.0 "]
    )
    def test_a_malformed_version_is_refused(self, version: str) -> None:
        """Leading zeros, wrong arity, and stray characters are all refused."""
        with pytest.raises(InvalidInputError):
            validate_version(version)


class TestValidateSha256Override:
    """`sha256-override`: empty is allowed, otherwise 64 lowercase hex."""

    def test_an_empty_override_is_accepted(self) -> None:
        """An empty override means "use the pinned table digest"."""
        validate_sha256_override("")

    def test_a_valid_override_is_accepted(self) -> None:
        """No exception is the whole assertion; validation is a pure check."""
        validate_sha256_override("a" * 64)

    def test_an_override_with_a_newline_is_refused(self) -> None:
        """A newline could smuggle an extra `GITHUB_OUTPUT` record; refused
        before any output is written.
        """
        with pytest.raises(InvalidInputError):
            validate_sha256_override("a" * 63 + "\n")

    def test_an_override_with_uppercase_hex_is_refused(self) -> None:
        """Only lowercase hex is accepted, matching the pinned table's own
        digests and `sha256sum`'s own output.
        """
        with pytest.raises(InvalidInputError):
            validate_sha256_override("A" * 64)


class TestResolveBinDir:
    """`bin-dir` validation: absolute or `~/`-relative, and creatable."""

    def test_a_tilde_relative_path_is_expanded_under_home(self) -> None:
        """`~/...` resolves under the real home directory, not a literal `~`."""
        result = resolve_bin_dir("~/.local/bin-test-install-makeutil")

        assert result.is_absolute()
        assert result.is_dir()
        result.rmdir()

    def test_an_absolute_path_is_created_and_returned(self, tmp_path: Path) -> None:
        """A caller-supplied absolute path is created if it does not exist."""
        target = tmp_path / "nested" / "bin"

        result = resolve_bin_dir(str(target))

        assert result == target.resolve()
        assert result.is_dir()

    @pytest.mark.parametrize(
        "bin_dir_input",
        [
            "relative/bin",
            "/abs/../parent",
            "/abs:with:colon",
            "x" * 241,
        ],
    )
    def test_a_malformed_bin_dir_is_refused(self, bin_dir_input: str) -> None:
        """Relative paths, parent-directory components, colons, and an
        over-long value are all refused before any directory is touched.
        """
        with pytest.raises(InvalidInputError):
            resolve_bin_dir(bin_dir_input)

    def test_a_newline_in_bin_dir_is_refused(self) -> None:
        """A newline could smuggle a second GITHUB_PATH line; refused first."""
        with pytest.raises(InvalidInputError):
            resolve_bin_dir("/opt/tools/bin\n/etc")

    def test_a_symlink_resolving_to_a_colon_in_its_path_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A `bin-dir` that is itself clean can still resolve, via a
        symlink, to a target whose path contains the PATH separator; the
        resolved path - what is actually published to `GITHUB_PATH` - must
        be checked too, not just the symlink's own spelling.
        """
        target = tmp_path / "with:colon"
        target.mkdir()
        link = tmp_path / "clean-link"
        link.symlink_to(target)

        with pytest.raises(InvalidInputError):
            resolve_bin_dir(str(link))

    def test_a_symlink_to_a_clean_directory_is_still_accepted(
        self, tmp_path: Path
    ) -> None:
        """A symlink whose target is unproblematic resolves normally; the
        revalidation must not reject a legitimate symlinked `bin-dir`.
        """
        target = tmp_path / "real-bin"
        target.mkdir()
        link = tmp_path / "link-to-bin"
        link.symlink_to(target)

        result = resolve_bin_dir(str(link))

        assert result == target.resolve()
