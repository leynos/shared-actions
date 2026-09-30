"""Target resolution, digest lookup, and input validation.

These are the pure decisions the `resolve` subcommand makes before anything
is downloaded: which target triple a runner maps to, which digest a version
is pinned to, and whether the caller's `version`/`bin-dir` inputs are even
well-formed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from makeutil_bin_dir import resolve_bin_dir
from makeutil_errors import (
    InvalidInputError,
    UnknownVersionError,
    UnsupportedPlatformError,
)
from makeutil_plan import (
    PlanRequest,
    build_asset_urls,
    build_cache_key,
    lookup_digest,
    resolve_plan,
    resolve_target,
    validate_sha256_override,
    validate_version,
)

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

    def test_the_pinned_0_1_1_digests_match_the_release_sidecars(self) -> None:
        """The digests are the ones the v0.1.1 release publishes in its
        `.sha256` files, not values computed here.
        """
        assert (
            lookup_digest("0.1.1", "x86_64-unknown-linux-musl")
            == "be86dd00994ffeb81fedaa660cf007366a5de87bccab5c73ec1153b2ad7fe26c"
        )
        assert (
            lookup_digest("0.1.1", "aarch64-unknown-linux-musl")
            == "8ec3eebd0e2af7cf0e087dc810defc7c065c3938a5179cdb0b3e25148659aaa4"
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
    """`bin-dir` validation: absolute or `~/`-relative, never created here."""

    def test_a_tilde_relative_path_is_expanded_under_the_injected_home(
        self, tmp_path: Path
    ) -> None:
        """`~/...` resolves under the home passed in, not a literal `~` and
        not the ambient home, so the query reads no process state.
        """
        result = resolve_bin_dir("~/.local/bin", tmp_path)

        assert result == (tmp_path / ".local" / "bin").resolve()
        assert not result.exists()

    def test_a_tilde_path_without_a_home_is_refused(self) -> None:
        """A runner with no home directory cannot expand `~/`."""
        with pytest.raises(InvalidInputError, match="no home directory"):
            resolve_bin_dir("~/.local/bin", None)

    @pytest.mark.parametrize("failure", [RuntimeError, OSError])
    def test_an_unresolvable_path_is_refused_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
    ) -> None:
        """`Path.resolve()` can raise `RuntimeError` (a symlink loop on older
        Pythons) or `OSError`; either must surface as the documented
        `InvalidInputError`, not escape as a traceback.
        """

        def _failing_resolve(self: Path, *_args: object, **_kwargs: object) -> Path:
            message = "simulated resolution failure"
            raise failure(message)

        monkeypatch.setattr(Path, "resolve", _failing_resolve)

        with pytest.raises(InvalidInputError, match="could not be resolved"):
            resolve_bin_dir("/opt/tools/bin", None)

    def test_an_absolute_path_is_returned_without_being_created(
        self, tmp_path: Path
    ) -> None:
        """Resolution is a query; a missing directory stays missing."""
        target = tmp_path / "nested" / "bin"

        result = resolve_bin_dir(str(target), None)

        assert result == target.resolve()
        assert not target.exists()

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
            resolve_bin_dir(bin_dir_input, None)

    def test_a_newline_in_bin_dir_is_refused(self) -> None:
        """A newline could smuggle a second GITHUB_PATH line; refused first."""
        with pytest.raises(InvalidInputError):
            resolve_bin_dir("/opt/tools/bin\n/etc", None)

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
            resolve_bin_dir(str(link), None)

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

        result = resolve_bin_dir(str(link), None)

        assert result == target.resolve()


class TestResolvePlan:
    """`resolve_plan` is a query: it decides and returns, and changes nothing."""

    def test_the_plan_is_returned_and_nothing_is_created(self, tmp_path: Path) -> None:
        """No output environment is needed, and a missing `bin-dir` stays so."""
        bin_dir = tmp_path / "missing" / "bin"

        plan = resolve_plan(PlanRequest("0.1.0", bin_dir, "", "Linux", "ARM64"))

        assert plan.target == "aarch64-unknown-linux-musl"
        assert plan.executable_path == bin_dir / "makeutil"
        assert plan.cache_key.endswith(plan.expected_sha256)
        assert dict(plan.outputs())["version"] == "0.1.0"
        assert not bin_dir.exists()
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            pytest.param({"version": "x"}, InvalidInputError, id="version"),
            pytest.param({"runner_os": "Windows"}, UnsupportedPlatformError, id="os"),
            pytest.param({"version": "0.0.1"}, UnknownVersionError, id="unpinned"),
        ],
    )
    def test_a_refusal_is_a_typed_error(
        self, tmp_path: Path, kwargs: dict[str, str], error: type[Exception]
    ) -> None:
        """Each refusal is an explicit error the command layer maps."""
        request = PlanRequest(
            version=kwargs.get("version", "0.1.0"),
            bin_dir=tmp_path / "bin",
            sha256_override="",
            runner_os=kwargs.get("runner_os", "Linux"),
            runner_arch="X64",
        )

        with pytest.raises(error):
            resolve_plan(request)
