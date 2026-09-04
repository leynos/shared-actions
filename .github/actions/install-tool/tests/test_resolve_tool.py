"""Tests for the manifest resolver.

The resolver is pure: it reads the manifest, prints `key=value` lines, and
performs no side effect. That is what lets every failure be a `status` line
rather than an exception, so one step decides what a failed resolution means
and there is a single place to look when a job stops.
"""

from __future__ import annotations

import subprocess
import sys
import typing as typ

import pytest
from _manifest import RESOLVE_SCRIPT_PATH, SUPPORTED_RUNNERS, TOOL_MANIFEST_PATH

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

MANIFEST = """
schema = 1

[[tool]]
name = "widget"
version = "1.2.3"
binary = "widget"
version-args = ["--version"]

  [[tool.target]]
  triple = "x86_64-unknown-linux-gnu"
  url = "https://github.com/example/widget/releases/download/v1.2.3/widget.tar.gz"
  sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  member = "widget-1.2.3/widget"
  sidecar = "match"

  [[tool.target]]
  triple = "x86_64-pc-windows-msvc"
  url = "https://github.com/example/widget/releases/download/v1.2.3/widget.zip"
  sha256 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
  member = "widget.exe"
  sidecar = "absent"

[[tool]]
name = "quiet"
version = "0.1.0"
binary = "quiet"
version-args = []

  [[tool.target]]
  triple = "x86_64-unknown-linux-gnu"
  url = "https://github.com/example/quiet/releases/download/v0.1.0/quiet.tgz"
  sha256 = "aaaabbbbccccddddeeeeffff00001111aaaabbbbccccddddeeeeffff00001111"
  member = "quiet"
  sidecar = "unchecked"
"""


def resolve(
    manifest_path: Path,
    tool: str,
    version: str,
    runner_os: str = "Linux",
    runner_arch: str = "X64",
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Run the resolver and return the process and its parsed output."""
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the shipped script.
        [
            sys.executable,
            str(RESOLVE_SCRIPT_PATH),
            "--manifest",
            str(manifest_path),
            "--tool",
            tool,
            "--version",
            version,
            "--runner-os",
            runner_os,
            "--runner-arch",
            runner_arch,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    fields = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    return completed, fields


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    """Write a small manifest covering the cases that matter."""
    path = tmp_path / "tool-manifest.toml"
    path.write_text(MANIFEST, encoding="utf-8")
    return path


class TestResolution:
    """The ordinary path, and what it publishes."""

    def test_resolves_an_entry_for_this_runner(self, manifest: Path) -> None:
        """Everything a later step needs, and nothing it has to derive."""
        completed, fields = resolve(manifest, "widget", "1.2.3")

        assert completed.returncode == 0, completed.stderr
        assert fields["status"] == "ok"
        assert fields["triple"] == "x86_64-unknown-linux-gnu"
        assert fields["member"] == "widget-1.2.3/widget"
        assert fields["extension"] == "tar.gz"
        assert fields["binary"] == "widget"
        assert fields["expected-version"] == "widget 1.2.3"
        assert fields["version-check"] == "true"

    def test_appends_exe_on_windows(self, manifest: Path) -> None:
        """The probe looks for a file by name, and Windows names it .exe."""
        _completed, fields = resolve(
            manifest, "widget", "1.2.3", runner_os="Windows", runner_arch="X64"
        )

        assert fields["binary"] == "widget.exe"
        assert fields["extension"] == "zip"

    def test_reports_a_tool_that_cannot_be_asked_its_version(
        self, manifest: Path
    ) -> None:
        """An empty argument list is a fact about the tool, not an omission.

        dylint-link refuses every argument without a rustup toolchain, so
        there is nothing to read back and the action says so rather than
        pretending it checked.
        """
        _completed, fields = resolve(manifest, "quiet", "0.1.0")

        assert fields["version-check"] == "false"
        assert fields["version-args"] == ""

    def test_carries_the_sidecar_state_through(self, manifest: Path) -> None:
        """Whether a pin has upstream corroboration reaches the log."""
        _completed, fields = resolve(manifest, "widget", "1.2.3")

        assert fields["sidecar"] == "match"


class TestFailures:
    """Every failure is data, and names what to do about it."""

    def test_an_unknown_tool_lists_what_there_is(self, manifest: Path) -> None:
        """The next question is always "then what is available?"."""
        completed, fields = resolve(manifest, "sprocket", "1.0.0")

        assert completed.returncode == 0, "resolution failures are data, not exits"
        assert fields["status"] == "error"
        assert fields["error-kind"] == "unknown-tool"
        assert "widget" in fields["error-message"]

    def test_an_unknown_version_says_to_add_it(self, manifest: Path) -> None:
        """The wrong fix is to make the version float, so it is named."""
        _completed, fields = resolve(manifest, "widget", "9.9.9")

        assert fields["error-kind"] == "unknown-version"
        assert "1.2.3" in fields["error-message"]
        assert "float" in fields["error-message"]

    def test_an_unsupported_runner_is_refused(self, manifest: Path) -> None:
        """A triple guessed for an unknown runner would be a wrong download."""
        _completed, fields = resolve(
            manifest, "widget", "1.2.3", runner_os="Plan9", runner_arch="X64"
        )

        assert fields["error-kind"] == "unsupported-runner"

    def test_an_unsupported_target_lists_what_is_offered(self, manifest: Path) -> None:
        """The dylint case: the tool exists, this platform does not."""
        _completed, fields = resolve(
            manifest, "widget", "1.2.3", runner_os="macOS", runner_arch="ARM64"
        )

        assert fields["error-kind"] == "unsupported-target"
        assert "x86_64-unknown-linux-gnu" in fields["error-message"]

    def test_an_unreadable_manifest_exits_two(self, tmp_path: Path) -> None:
        """A broken manifest is this repository's defect, not a caller's.

        It is the one condition that is not a resolvable outcome, so it is
        the one that exits non-zero.
        """
        broken = tmp_path / "broken.toml"
        broken.write_text("this is not toml = = =", encoding="utf-8")

        completed, fields = resolve(broken, "widget", "1.2.3")

        assert completed.returncode == 2
        assert fields["error-kind"] == "manifest-unreadable"


class TestAgainstTheRealManifest:
    """The shipped manifest, resolved for every runner it claims to serve."""

    @pytest.mark.parametrize(
        ("runner_os", "runner_arch", "triple"),
        [(os, arch, triple) for (os, arch), triple in SUPPORTED_RUNNERS.items()],
    )
    def test_sccache_resolves_on_every_supported_runner(
        self, runner_os: str, runner_arch: str, triple: str
    ) -> None:
        """Only sccache is offered on every runner the action resolves."""
        completed, fields = resolve(
            TOOL_MANIFEST_PATH, "sccache", "0.17.0", runner_os, runner_arch
        )

        assert completed.returncode == 0, completed.stderr
        assert fields["status"] == "ok"
        assert fields["triple"] == triple

    def test_dylint_fails_closed_off_linux(self) -> None:
        """No macOS or Windows archive exists, so it must refuse to guess."""
        _completed, fields = resolve(
            TOOL_MANIFEST_PATH, "cargo-dylint", "6.0.4", "macOS", "ARM64"
        )

        assert fields["error-kind"] == "unsupported-target"
