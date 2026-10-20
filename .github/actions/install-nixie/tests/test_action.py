"""Verify the version-aware, binary-only install-nixie action contract."""

from __future__ import annotations

import os
import typing as typ

import pytest
from _install_nixie_test_support import (
    BINSTALL_ACTION_PIN,
    BINSTALL_VERSION,
    INSTALL_STEP_ID,
    PROBE_STEP_ID,
    SUPPORTED_TARGETS,
    _assert_github_path_empty,
    _calls,
    _expected_binstall_call,
    _install_script,
    _InstallOptions,
    _load_action,
    _ProbeOptions,
    _run_install_script,
    _run_probe,
    _step_with_id,
    _token_observations,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_caller_token_reaches_cargo_binstall_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Authenticate the release lookup without handing out the credential."""
    options = _InstallOptions(
        merman_cached=False,
        github_token="caller-job-token",  # noqa: S106 - a stand-in, not a credential
    )
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        tmp_path / "cache-home" / "merman" / "0.7.0" / "bin",
        "0.7.0",
        SUPPORTED_TARGETS[("Linux", "X64")],
    )
    # The freshly installed executable is probed for its version. That probe
    # must not see the token, and neither may the stub cargo-binstall itself
    # once it has recorded what its own invocation received.
    assert _token_observations(tmp_path) == [
        "binstall token=<caller-job-token>",
        "merman token=<unset>",
        "uv token=<unset>",
        "uv token=<unset>",
    ]


def test_a_job_without_a_token_leaves_the_lookup_unauthenticated(
    tmp_path: Path,
) -> None:
    """An absent job token must not become an empty credential."""
    options = _InstallOptions(merman_cached=False, github_token=None)
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        tmp_path / "cache-home" / "merman" / "0.7.0" / "bin",
        "0.7.0",
        SUPPORTED_TARGETS[("Linux", "X64")],
    )
    assert "binstall token=<>" not in _token_observations(tmp_path)
    assert _token_observations(tmp_path)[0] == "binstall token=<unset>"


def test_install_step_maps_the_job_token_under_its_restricted_name() -> None:
    """The token enters the step under a name no other child reads."""
    env = _step_with_id(INSTALL_STEP_ID)["env"]

    assert env["MERMAN_GITHUB_TOKEN"] == "${{ github.token }}"  # noqa: S105 - an expression, not a secret.
    assert "GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env


def test_manifest_preserves_input_defaults_and_selects_binary_only_merman() -> None:
    """Expose existing pins and install Merman with exact, binary-only settings."""
    manifest = _load_action()
    script = _install_script()

    assert manifest["runs"]["using"] == "composite"
    assert manifest["inputs"]["nixie-version"]["default"] == "1.1.0"
    assert manifest["inputs"]["merman-version"]["default"] == "0.7.0"
    assert manifest["inputs"]["python-version"]["default"] == "3.14"
    assert "cargo binstall" in script
    assert "--no-confirm" in script
    assert "--disable-strategies=compile" in script
    assert '--version "=${MERMAN_VERSION}"' in script
    assert '--install-path "$merman_bin_dir"' in script
    assert '--targets "$merman_target"' in script
    assert "cargo install" not in script
    for obsolete_downloader in ("curl", "shasum", "tar -", "merman_checksum"):
        assert obsolete_downloader not in script
    assert all(
        f"{runner_os}/{runner_arch}" in script
        for runner_os, runner_arch in SUPPORTED_TARGETS
    )


def test_action_prunes_the_callers_restored_cache_before_uv_install() -> None:
    """Remove restored script environments before the atomic uv installer."""
    steps = _load_action()["runs"]["steps"]
    names = [step.get("name") for step in steps]
    prune_index = names.index("Prune restored uv cache")
    install_index = names.index("Install Nixie and Merman CLI")

    assert prune_index < install_index, (
        f"prune step at {prune_index} must precede install step at {install_index}"
    )
    assert "prune_uv_cache.sh" in steps[prune_index]["run"], (
        "prune step must run the shared prune_uv_cache.sh script"
    )


def test_manifest_orders_probe_installer_and_pins_upstream_action() -> None:
    """Probe first, conditionally install the pinned tool, then install CLIs."""
    steps = _load_action()["runs"]["steps"]
    step_ids = [step.get("id") for step in steps]
    probe = _step_with_id(PROBE_STEP_ID)
    installer = next(
        step for step in steps if step.get("name") == "Install cargo-binstall"
    )
    cli_index = step_ids.index(INSTALL_STEP_ID)

    assert step_ids.index(PROBE_STEP_ID) < steps.index(installer) < cli_index
    assert probe["run"].splitlines() == [
        "if cargo binstall -V >/dev/null 2>&1; then",
        '  echo "installed=true" >> "${GITHUB_OUTPUT}"',
        "else",
        '  echo "installed=false" >> "${GITHUB_OUTPUT}"',
        "fi",
    ]
    assert installer["if"] == "steps.check-cargo-binstall.outputs.installed != 'true'"
    action, separator, pin = installer["uses"].partition("@")
    assert action == "cargo-bins/cargo-binstall"
    assert separator
    assert pin == BINSTALL_ACTION_PIN
    assert len(pin) == 40
    assert all(character in "0123456789abcdef" for character in pin)
    assert installer["with"] == {"version": BINSTALL_VERSION}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(
            _ProbeOptions(
                cargo_present=False,
                binstall_present=False,
                binstall_exit_status=127,
            ),
            "installed=false",
            id="cargo-absent",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=False,
                binstall_exit_status=127,
            ),
            "installed=false",
            id="subcommand-absent",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=True,
                binstall_exit_status=23,
            ),
            "installed=false",
            id="discoverable-shim-fails",
        ),
        pytest.param(
            _ProbeOptions(
                cargo_present=True,
                binstall_present=True,
                binstall_exit_status=0,
            ),
            "installed=true",
            id="usable-subcommand",
        ),
    ],
)
def test_probe_writes_exact_result_and_swallows_unavailable_command(
    tmp_path: Path,
    options: _ProbeOptions,
    expected: str,
) -> None:
    """A missing or unusable Cargo subcommand is a false result, not step failure."""
    result = _run_probe(
        tmp_path,
        cargo_present=options.cargo_present,
        binstall_present=options.binstall_present,
        binstall_exit_status=options.binstall_exit_status,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == f"{expected}\n"
    expected_calls = ["cargo <binstall -V>"] if options.cargo_present else []
    if options.cargo_present and options.binstall_present:
        expected_calls.append("cargo-binstall <-V>")
    assert _calls(tmp_path, probe=True) == expected_calls


def test_cached_merman_is_reused_only_when_it_reports_the_requested_version(
    tmp_path: Path,
) -> None:
    """Reuse a working version-specific Merman cache and export its bin directory."""
    result = _run_install_script(tmp_path, _InstallOptions())

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    expected_path = (
        f"{(tmp_path / 'cache-home' / 'merman' / '0.7.0' / 'bin').as_posix()}\n"
        f"{(tmp_path / 'uv-bin').as_posix()}\n"
    )
    assert (tmp_path / "github-path").read_text(encoding="utf-8") == expected_path


def test_wrong_version_cache_is_reconciled_before_export(tmp_path: Path) -> None:
    """Replace a cached executable that reports a different Merman version."""
    options = _InstallOptions(cached_merman_version="0.6.9")
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert "Merman cache refresh" in result.stdout
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
        ),
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()[0] == (
        merman_bin_dir.as_posix()
    )


def test_unusable_cached_executable_is_reinstalled(tmp_path: Path) -> None:
    """Repair a cache entry whose version command exits unsuccessfully."""
    options = _InstallOptions(cached_merman_exit_status=41)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
    )


@pytest.mark.skipif(
    os.name == "nt", reason="Windows does not preserve POSIX executable mode bits"
)
def test_non_executable_cached_merman_is_reinstalled(tmp_path: Path) -> None:
    """A cached file without executable permission must not bypass installation."""
    options = _InstallOptions(cached_merman_executable=False)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert "Merman cache refresh" in result.stdout
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
    )


@pytest.mark.parametrize(
    "merman_version",
    ["0.8.3", "1.2.3"],
    ids=["new-minor", "new-major"],
)
def test_install_script_forwards_the_exact_requested_version(
    tmp_path: Path, merman_version: str
) -> None:
    """Install a numeric Merman version with source compilation disabled."""
    options = _InstallOptions(merman_cached=False, merman_version=merman_version)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / merman_version / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, merman_version, SUPPORTED_TARGETS[("Linux", "X64")]
        ),
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
    ]
    assert (merman_bin_dir / "merman-cli").is_file()


@pytest.mark.parametrize(
    "merman_version",
    ["0.7", "v0.7.0", "01.2.3", "0.7.0-rc.1", "not-a-version"],
)
def test_install_script_rejects_non_numeric_merman_versions_before_binstall(
    tmp_path: Path, merman_version: str
) -> None:
    """Require a three-component numeric version before invoking cargo-binstall."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, merman_version=merman_version),
    )

    assert result.returncode == 1
    assert "merman-version must use three numeric components" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("runner_os", "runner_arch", "target"),
    [
        pytest.param("Linux", "X64", "x86_64-unknown-linux-gnu", id="linux-x64"),
        pytest.param("macOS", "ARM64", "aarch64-apple-darwin", id="macos-arm64"),
        pytest.param("macOS", "X64", "x86_64-apple-darwin", id="macos-x64"),
        pytest.param("Windows", "X64", "x86_64-pc-windows-msvc", id="windows-x64"),
    ],
)
def test_install_script_selects_a_published_binary_target_and_exports_its_directory(
    tmp_path: Path, runner_os: str, runner_arch: str, target: str
) -> None:
    """Use each supported Merman release target and export the executable path."""
    options = _InstallOptions(
        merman_cached=False,
        runner_arch=runner_arch,
        runner_os=runner_os,
        uv_bin_dir_output=r"C:\Users\runneradmin\.local\bin"
        if runner_os == "Windows"
        else None,
    )
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path)[0] == _expected_binstall_call(
        merman_bin_dir, "0.7.0", target
    )
    path_lines = (tmp_path / "github-path").read_text(encoding="utf-8").splitlines()
    if runner_os == "Windows":
        assert path_lines == [
            r"C:\runner\cache\merman\0.7.0\bin",
            r"C:\Users\runneradmin\.local\bin",
        ]
    else:
        assert path_lines == [
            merman_bin_dir.as_posix(),
            (tmp_path / "uv-bin").as_posix(),
        ]


def test_install_failure_stops_nixie_and_does_not_export_path(tmp_path: Path) -> None:
    """A failed binary install must stop before Nixie and PATH publication."""
    options = _InstallOptions(merman_cached=False, merman_install_status=17)
    result = _run_install_script(tmp_path, options)
    merman_bin_dir = tmp_path / "cache-home" / "merman" / "0.7.0" / "bin"

    assert result.returncode == 17
    assert "Merman binary installation failed" in result.stderr
    assert _calls(tmp_path) == [
        _expected_binstall_call(
            merman_bin_dir, "0.7.0", SUPPORTED_TARGETS[("Linux", "X64")]
        )
    ]
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        pytest.param(
            _InstallOptions(merman_cached=False, install_creates_merman=False),
            "cargo-binstall did not create a usable executable",
            id="missing-executable",
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, install_merman_executable=False),
            "cargo-binstall did not create a usable executable",
            id="non-executable-file",
            marks=pytest.mark.skipif(
                os.name == "nt",
                reason="Windows does not preserve POSIX executable mode bits",
            ),
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, installed_merman_version="0.6.9"),
            "installed merman-cli did not report requested version",
            id="wrong-installed-version",
        ),
        pytest.param(
            _InstallOptions(merman_cached=False, installed_merman_exit_status=42),
            "installed merman-cli did not report requested version",
            id="unusable-installed-executable",
        ),
    ],
)
def test_install_script_rejects_missing_or_unusable_installed_merman(
    tmp_path: Path,
    options: _InstallOptions,
    message: str,
) -> None:
    """Do not continue when cargo-binstall fails to leave the requested CLI."""
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 1
    assert message in result.stderr
    assert not any(call.startswith("uv <tool install") for call in _calls(tmp_path))
    _assert_github_path_empty(tmp_path)


def test_install_script_rejects_an_unsupported_runner_target(tmp_path: Path) -> None:
    """Report unsupported operating-system and architecture pairs clearly."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(merman_cached=False, runner_arch="ARM64"),
    )

    assert result.returncode == 1
    assert "Unsupported Merman platform" in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (_InstallOptions(nixie_version="1.1"), "nixie-version must use"),
        (_InstallOptions(python_version="3"), "python-version must use"),
    ],
)
def test_install_script_rejects_malformed_nixie_or_python_versions(
    tmp_path: Path,
    options: _InstallOptions,
    message: str,
) -> None:
    """Malformed Nixie or Python version pins stop before either installation."""
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 1
    assert message in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)


def test_install_script_repairs_only_a_missing_nixie_shim(tmp_path: Path) -> None:
    """A missing Nixie shim should trigger exactly one forced reconciliation."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(nixie_shim_after_install=False),
    )

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.14 nixie-cli==1.1.0>",
        "uv <tool dir --bin>",
        "uv <tool install --force --python 3.14 nixie-cli==1.1.0>",
    ]


def test_install_script_fails_when_forced_reconciliation_leaves_no_shim(
    tmp_path: Path,
) -> None:
    """A failed Nixie shim repair should prevent PATH export."""
    result = _run_install_script(
        tmp_path,
        _InstallOptions(
            nixie_shim_after_install=False,
            nixie_shim_after_force=False,
        ),
    )

    assert result.returncode == 1
    assert "nixie was not installed" in result.stderr
    _assert_github_path_empty(tmp_path)


def test_install_script_forwards_nixie_and_python_overrides_through_uv(
    tmp_path: Path,
) -> None:
    """Nixie and Python overrides reach normal uv reconciliation unchanged."""
    options = _InstallOptions(nixie_version="1.2.3", python_version="3.13")
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "uv <tool install --python 3.13 nixie-cli==1.2.3>",
        "uv <tool dir --bin>",
    ]


def test_install_script_stops_after_nixie_install_failure(tmp_path: Path) -> None:
    """Avoid shim repair and PATH export after normal Nixie reconciliation fails."""
    result = _run_install_script(tmp_path, _InstallOptions(uv_install_status=19))

    assert result.returncode == 19
    assert _calls(tmp_path) == ["uv <tool install --python 3.14 nixie-cli==1.1.0>"]
    _assert_github_path_empty(tmp_path)


@pytest.mark.parametrize(
    ("options", "expected_error"),
    [
        pytest.param(
            _InstallOptions(include_uv=False, merman_cached=False),
            "uv is required to install nixie-cli",
            id="missing-uv",
        ),
        pytest.param(
            _InstallOptions(include_cargo=False, merman_cached=False),
            "cargo is required to run cargo-binstall for merman-cli",
            id="missing-cargo",
        ),
    ],
)
def test_install_script_reports_a_missing_prerequisite_before_install(
    tmp_path: Path,
    options: _InstallOptions,
    expected_error: str,
) -> None:
    """Stop before installing anything when a prerequisite is unavailable."""
    result = _run_install_script(tmp_path, options)

    assert result.returncode == 1
    assert expected_error in result.stderr
    assert _calls(tmp_path) == []
    _assert_github_path_empty(tmp_path)
