"""Verify the install-whitaker action manifest's declared contract.

These tests read ``action.yml`` only. They assert the input table, the step
ordering, the cache wiring, and the environment each lifecycle step declares,
so a change to the manifest that breaks a caller shows up here rather than in a
workflow run. The executable behaviour lives in the sibling install and input
modules. Run the suite with ``uv run pytest
.github/actions/install-whitaker/tests``.
"""

from __future__ import annotations

import re
import string
import typing as typ

from _action_manifest import (
    DIGEST_MANIFEST_PATH,
    LIFECYCLE_STEP_NAMES,
    RESOLVE_SCRIPT_PATH,
    load_manifest,
    manifest_steps,
    step_by_name,
)

_PINNED_TARGETS = (
    "aarch64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
)
_PINNED_VERSIONS = ("0.2.6", "0.2.7", "0.2.8")


def _step_env(name: str) -> dict[str, str]:
    """Return the rendered environment mapping a step declares."""
    return typ.cast("dict[str, str]", step_by_name(name)["env"])


def _resolution_query() -> str:
    """Return the pure resolution script the adapter step runs."""
    return RESOLVE_SCRIPT_PATH.read_text(encoding="utf-8")


def _step_script(name: str) -> str:
    """Return the Bash fragment a step declares."""
    script = step_by_name(name)["run"]
    assert isinstance(script, str)
    return script


class TestInputs:
    """Validate the action's declared input contract."""

    def test_declares_the_documented_inputs(self) -> None:
        """Verify every input, its default, and its description."""
        assert load_manifest()["inputs"] == {
            "cargo-home": {
                "description": (
                    "Cargo home that stores the cached whitaker-installer binary"
                ),
                "required": False,
                "default": "~/.cargo",
            },
            "installer-version": {
                "description": "Version of whitaker-installer to install",
                "required": False,
                "default": "0.2.8",
            },
            "installer-sha256": {
                "description": (
                    "SHA-256 digest of the whitaker-installer release archive "
                    "for this runner. The action's pinned digest manifest takes "
                    "precedence; supply this only for an asset the manifest does "
                    "not pin. A value that disagrees with a pinned digest is "
                    "rejected."
                ),
                "required": False,
                "default": "",
            },
            "suite-version": {
                "description": (
                    "Git reference the lint suite is built from: a tag, a "
                    "branch or a commit. Left empty, the installer builds the "
                    "suite from the Whitaker default branch tip, so a change "
                    "there alters lint results with no commit in this "
                    "repository. Pinning makes a suite change arrive as a "
                    "reviewed bump, at the cost of a source build, because "
                    "prebuilt lint libraries are published only for the tip. "
                    "Requires installer 0.2.8 or later."
                ),
                "required": False,
                "default": "",
            },
            "cache-provider": {
                "description": (
                    'Cache owner for the installer binary. Use "github" for the '
                    'action\'s built-in cache or "external" when the caller mounts '
                    "the Cargo home."
                ),
                "required": False,
                "default": "github",
            },
            "ci-mode": {
                "description": (
                    "Treat a source build as a failure rather than a slow "
                    "success. CI is meant to consume Whitaker's published "
                    "binaries, so a run that built the lint suite or the Dylint "
                    "tools from source has silently changed what it tested and "
                    "how long it took. With this on, the action checks the "
                    "published assets before starting, retries a short absence, "
                    "and fails the step if the installer still resorted to a "
                    "source build. Set it off only for local reproduction, "
                    "where a source build is a legitimate choice."
                ),
                "required": False,
                "default": "true",
            },
            "allow-suite-pin": {
                "description": (
                    "Permit suite-version while ci-mode is on. A pin forces a "
                    "source build, because prebuilt lint libraries are "
                    "published only for the branch tip, so the two settings "
                    "contradict each other unless the caller says otherwise "
                    "deliberately."
                ),
                "required": False,
                "default": "false",
            },
            "github-token": {
                "description": (
                    "Token used only to read the public rolling release without "
                    "meeting the unauthenticated rate limit. It is never sent "
                    "anywhere else."
                ),
                "required": False,
                "default": "${{ github.token }}",
            },
        }


class TestStepOrdering:
    """Validate the composite step sequence."""

    def test_runs_validation_cache_then_the_lifecycle(self) -> None:
        """Verify the declared steps and their order."""
        names = [step["name"] for step in manifest_steps()]

        assert names == [
            "Validate Whitaker inputs",
            "Cache Whitaker installer",
            *LIFECYCLE_STEP_NAMES,
        ]

    def test_every_lifecycle_step_uses_bash(self) -> None:
        """Verify each lifecycle step runs an inline Bash fragment."""
        for name in LIFECYCLE_STEP_NAMES:
            step = step_by_name(name)
            assert step["shell"] == "bash"
            assert isinstance(step["run"], str)


class TestValidationStep:
    """Validate the input-validation step's contract."""

    def test_declares_every_validated_input(self) -> None:
        """Verify the validation step receives every input it judges."""
        assert _step_env("Validate Whitaker inputs") == {
            "ALLOW_SUITE_PIN_INPUT": "${{ inputs.allow-suite-pin }}",
            "CACHE_PROVIDER_INPUT": "${{ inputs.cache-provider }}",
            "CARGO_HOME_INPUT": "${{ inputs.cargo-home }}",
            "CI_MODE_INPUT": "${{ inputs.ci-mode }}",
            "INSTALLER_SHA256_INPUT": "${{ inputs.installer-sha256 }}",
            "INSTALLER_VERSION_INPUT": "${{ inputs.installer-version }}",
            "SUITE_VERSION_INPUT": "${{ inputs.suite-version }}",
        }

    def test_states_every_rejection_reason(self) -> None:
        """Verify each documented rejection message is present."""
        script = _step_script("Validate Whitaker inputs")

        for message in (
            "must not contain a carriage return or newline",
            "must be an absolute path or start with ~/",
            "must not contain the runner PATH separator",
            "without leading zeros",
            "cache-provider must be github or external",
            "installer-sha256 must be 64 hexadecimal characters",
        ):
            assert message in script


class TestCacheSteps:
    """Validate the cache wiring and its reporting."""

    def test_caches_the_installer_and_the_installed_suite(self) -> None:
        """Verify the cache action, its gate, paths, and key."""
        step = step_by_name("Cache Whitaker installer")

        assert step["id"] == "cache-whitaker-installer"
        assert step["uses"] == (
            "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
        )
        assert step["if"] == "${{ inputs.cache-provider == 'github' }}"
        config = typ.cast("dict[str, str]", step["with"])
        assert config["path"].splitlines() == [
            "${{ steps.validate-inputs.outputs.installer-path }}",
            "${{ steps.validate-inputs.outputs.installer-version-path }}",
            "~/.local/share/whitaker",
        ]
        assert config["key"] == (
            "whitaker-${{ runner.os }}-${{ runner.arch }}-"
            "${{ steps.validate-inputs.outputs.installer-version }}-"
            "${{ hashFiles('dylint.toml') }}-"
            "${{ steps.validate-inputs.outputs.cargo-home }}"
        )

    def test_reports_the_cache_provider_and_outcome(self) -> None:
        """Verify the cache-reporting step reads the provider and hit."""
        env = _step_env("Report Whitaker installer cache")

        assert env["WHITAKER_CACHE_PROVIDER"] == "${{ inputs.cache-provider }}"
        assert env["WHITAKER_INSTALLER_CACHE_HIT"] == (
            "${{ steps.cache-whitaker-installer.outputs.cache-hit }}"
        )
        assert "provider=${WHITAKER_CACHE_PROVIDER}" in _step_script(
            "Report Whitaker installer cache",
        )


class TestLifecycleSteps:
    """Validate the release lifecycle's declared boundaries."""

    def test_resolution_reads_the_pinned_manifest_and_the_input(self) -> None:
        """Verify the resolve step's trust-anchor inputs."""
        env = _step_env("Resolve Whitaker release")

        assert env["RUNNER_ARCHITECTURE"] == "${{ runner.arch }}"
        assert env["RUNNER_OPERATING_SYSTEM"] == "${{ runner.os }}"
        assert env["WHITAKER_DIGEST_MANIFEST"] == (
            "${{ github.action_path }}/installer-digests.sha256"
        )
        assert env["WHITAKER_INSTALLER_SHA256"] == (
            "${{ steps.validate-inputs.outputs.installer-sha256 }}"
        )

    def test_resolution_records_the_lifecycle_contract(self) -> None:
        """Verify the resolve query prints every field publication needs."""
        query = _resolution_query()

        for field in (
            "status=install",
            "status=cached",
            "status=error",
            "asset=%s",
            "extension=%s",
            "installer-name=%s",
            "expected-sha=%s",
            "trust-anchor=%s",
            "staging-dir=%s",
        ):
            assert field in query

    def test_resolution_query_performs_no_externally_visible_write(self) -> None:
        """Verify the query itself writes nothing and annotates nothing."""
        query = _resolution_query()

        for effect in (
            "GITHUB_OUTPUT",
            "GITHUB_STEP_SUMMARY",
            "emit_metric",
            "::notice",
            "::error",
            ">>",
            '> "',
        ):
            assert effect not in query

    def test_resolution_step_is_a_thin_adapter(self) -> None:
        """Verify the step only runs the query script and writes its record."""
        script = _step_script("Resolve Whitaker release")

        assert script.count("$GITHUB_OUTPUT") == 1
        assert "resolution<<WHITAKER_RESOLUTION_EOF" in script
        assert 'resolution="$(bash "$WHITAKER_RESOLVE_SCRIPT")"' in script
        assert "resolve_target" not in script
        assert "pinned_digest" not in script
        assert _step_env("Resolve Whitaker release")["WHITAKER_RESOLVE_SCRIPT"] == (
            "${{ github.action_path }}/scripts/resolve-release.sh"
        )

    def test_resolution_reports_only_unexpected_internal_failure(self) -> None:
        """Verify the resolve step's only annotation is its ERR trap."""
        script = _step_script("Resolve Whitaker release")

        assert "set -Eeuo pipefail" in script
        assert script.count("::error") == 1
        assert "whitaker-installer.failure=resolve" in script

    def test_resolution_rejects_a_stale_cached_installer(self) -> None:
        """Verify a cached installer is reused only when its version matches."""
        query = _resolution_query()

        assert "cached_installer_version" in query
        assert 'cached_version" == "$WHITAKER_INSTALLER_VERSION' in query
        assert "stale-version=%s" in query

    def test_publication_writes_every_lifecycle_output(self) -> None:
        """Verify the publication step emits every output later steps consume."""
        script = _step_script("Publish Whitaker resolution")

        for output in (
            "needs-install=",
            "asset=%s",
            "extension=%s",
            "installer-name=%s",
            "expected-sha=%s",
            "trust-anchor=%s",
            "staging-dir=%s",
        ):
            assert output in script

    def test_publication_owns_the_resolution_annotations(self) -> None:
        """Verify the publication step reports the cache and failure outcomes."""
        script = _step_script("Publish Whitaker resolution")

        assert "whitaker-installer.path=cache" in script
        assert "whitaker-installer.digest=conflict" in script
        assert "whitaker-installer.digest=unpinned" in script
        assert "whitaker-installer.failure=install" in script
        assert "::error title=Whitaker installer failed::" in script

    def test_download_records_transfer_telemetry(self) -> None:
        """Verify each transfer reports its outcome, size, time, and attempts."""
        script = _step_script("Download Whitaker release")

        assert "--write-out" in script
        for placeholder in (
            "%{http_code}",
            "%{size_download}",
            "%{time_total}",
            "%{num_retries}",
        ):
            assert placeholder in script
        assert "whitaker-installer.transfer." in script
        assert "::notice title=Whitaker installer transfer::" in script
        assert 'fetch "${release_url}/${WHITAKER_ASSET}"' in script
        assert 'fetch "${release_url}/${WHITAKER_ASSET}.sha256"' in script

    def test_download_uses_the_pinned_release_over_https(self) -> None:
        """Verify the download step's URL and transport constraints."""
        script = _step_script("Download Whitaker release")

        assert "releases/download/v${WHITAKER_INSTALLER_VERSION}" in script
        assert "curl -fsSL --proto '=https' --tlsv1.2" in script

    def test_download_bounds_and_retries_each_transfer(self) -> None:
        """Verify transient network failures are retried within a bound."""
        script = _step_script("Download Whitaker release")

        for flag in (
            "--retry 3",
            "--retry-delay 2",
            "--retry-all-errors",
            "--connect-timeout 20",
            "--max-time 300",
        ):
            assert flag in script

    def test_extraction_chooses_its_tool_by_the_asset_extension(self) -> None:
        """Verify the extractor follows the archive format, not PATH order.

        Probing what `tar` is chose GNU tar on a GitHub-hosted Windows runner,
        because the step's Git Bash puts MSYS2's tar ahead of the Windows
        system directory, and GNU tar cannot read the `.zip` asset (#446). The
        step must branch on the resolved extension instead.
        """
        script = _step_script("Extract Whitaker installer")

        assert 'case "$WHITAKER_EXTENSION" in' in script
        assert "zip)" in script
        assert "tar.gz|tgz" in script
        # The tarball arm keeps the GNU probe, which is about a flag bsdtar
        # rejects rather than about which formats each tool can read.
        assert ('tar --force-local -xf "$1" --strip-components=1 -C "$2"') in script
        assert 'tar -xf "$1" --strip-components=1 -C "$2"' in script

    def test_an_unknown_archive_extension_fails_closed(self) -> None:
        """Verify an unrecognized extension is refused, not handed to tar.

        Falling through to `tar` is how a zip reached GNU tar in the first
        place, so the default arm must exit rather than guess.
        """
        script = _step_script("Extract Whitaker installer")

        default_arm = script.split("*)", 1)[1]
        assert "unsupported archive extension" in default_arm
        assert "exit 1" in default_arm

    def test_extraction_never_depends_on_unzip(self) -> None:
        """Verify no extraction arm invokes `unzip`.

        Some Windows runner images do not ship it, so the zip arm uses the
        Windows system tar, which is bsdtar, and falls back to a Python
        extractor this action ships.
        """
        script = _step_script("Extract Whitaker installer")

        commands = [
            line.strip()
            for line in script.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not any("unzip" in command for command in commands)
        assert "C:\\Windows\\System32\\tar.exe" in script
        assert "WHITAKER_ZIP_SCRIPT" in script

    def test_verification_compares_the_anchor_then_the_sidecar(self) -> None:
        """Verify the verify step's digest comparisons and metrics."""
        script = _step_script("Verify Whitaker release")

        assert "sha256sum" in script
        assert "whitaker-installer.digest=mismatch" in script
        assert "whitaker-installer.digest=sidecar-mismatch" in script
        assert "whitaker-installer.digest=verified" in script
        assert "whitaker-installer.trust-anchor=" in script

    def test_no_lifecycle_step_invokes_cargo(self) -> None:
        """Verify no fragment can fall back to a Cargo installation.

        Command position, not mere occurrence. One fragment now greps the
        installer's output for the phrase it prints when it falls back to
        `cargo install`, and a substring check cannot tell a detector from an
        invocation. Anchoring to a command boundary keeps the rule that
        matters: nothing here may run Cargo.
        """
        invocation = re.compile(
            r"""(?:^|[;&|(]|\$\(|`|\bthen\b|\bdo\b|\belse\b)\s*
                (?:[A-Za-z_]\w*=\S*\s+)*
                cargo\s+(?:install|binstall)\b""",
            re.VERBOSE | re.MULTILINE,
        )
        for name in LIFECYCLE_STEP_NAMES:
            script = _step_script(name)
            offenders = [
                line
                for line in script.splitlines()
                if not line.lstrip().startswith("#") and invocation.search(line)
            ]
            assert not offenders, f"{name} invokes Cargo: {offenders}"

    def test_installation_reads_the_resolved_installer_name(self) -> None:
        """Verify the install step installs the resolved filename."""
        env = _step_env("Install Whitaker installer")

        assert env["WHITAKER_INSTALLER_NAME"] == (
            "${{ steps.publish-resolution.outputs.installer-name }}"
        )
        assert env["WHITAKER_INSTALLER_VERSION_PATH"] == (
            "${{ steps.validate-inputs.outputs.installer-version-path }}"
        )
        assert env["WHITAKER_INSTALLER_PATH"] == (
            "${{ steps.validate-inputs.outputs.installer-path }}"
        )

    def test_execution_runs_the_installed_binary(self) -> None:
        """Verify the run step executes the installer it was given."""
        env = _step_env("Run Whitaker installer")
        script = _step_script("Run Whitaker installer")

        assert env["WHITAKER_INSTALLER_PATH"] == (
            "${{ steps.validate-inputs.outputs.installer-path }}"
        )
        assert '"$WHITAKER_INSTALLER_PATH"' in script
        assert "title=Whitaker installer::status=complete" in script


class TestPinnedDigestManifest:
    """Validate the checked-in trust anchor for installer archives."""

    def test_pins_every_supported_version_and_target(self) -> None:
        """Verify each supported version and target has a pinned digest."""
        entries = {
            asset: digest
            for digest, asset in (
                line.split()
                for line in DIGEST_MANIFEST_PATH.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line and not line.startswith("#")
            )
        }

        expected = {
            (
                f"whitaker-installer-{target}-v{version}."
                f"{'zip' if target.endswith('windows-msvc') else 'tgz'}"
            )
            for version in _PINNED_VERSIONS
            for target in _PINNED_TARGETS
        }
        assert entries.keys() == expected
        assert all(
            len(digest) == 64 and set(digest) <= set(string.hexdigits.lower())
            for digest in entries.values()
        )
