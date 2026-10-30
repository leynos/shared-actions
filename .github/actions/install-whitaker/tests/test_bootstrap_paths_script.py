"""Contracts for `scripts/bootstrap-paths.sh` against the installer's real output.

`fixtures/dry-run-0.2.11-installed.txt` is the report of the published
whitaker-installer 0.2.11 binary's `--dry-run --skip-deps` on a completed
installation (only the home directory was rewritten to `/home/runner`), and
`fixtures/bootstrap-0.2.11.json` is the `bootstrap.json` that installation
wrote. They were captured from the release asset, not written by hand, so the
parser is held to what the installer actually prints.
"""

from __future__ import annotations

import json
import subprocess
import typing as typ

import pytest
from _action_manifest import ACTION_DIR

from composite_fragments import ambient_env, bash_executable, require_posix_host

if typ.TYPE_CHECKING:
    from pathlib import Path

SCRIPT = ACTION_DIR / "scripts" / "bootstrap-paths.sh"
FIXTURES = ACTION_DIR / "tests" / "fixtures"
COMMIT = "e4608a25c2e20116fbc1e97a51a73f094a1db7a1"

require_posix_host()


def _run(*args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,TID251 - run the script under test.
        [bash_executable(), str(SCRIPT), *args],
        input=stdin,
        env={**ambient_env(), **env},
        capture_output=True,
        text=True,
        check=False,
    )


class TestParse:
    """Reading the installer's own report."""

    def test_the_real_report_names_the_generation_and_driver_directory(self) -> None:
        """The labels the action reads are the ones the installer prints."""
        report = (FIXTURES / "dry-run-0.2.11-installed.txt").read_text("utf-8")
        done = _run("parse", stdin=report)
        assert done.returncode == 0, done.stderr
        lines = dict(line.split("=", 1) for line in done.stdout.splitlines())
        root = "/home/runner/.local/share/whitaker-bootstrap"
        assert lines == {
            "generation": f"{root}/clippy/{COMMIT}",
            "state": "installed",
            "driver-directory": f"{root}/drivers/dylint-6.3.0/clippy-{COMMIT}",
        }, "expected: lines == {'generation': f'{root}/clippy/{COMMIT}', 'state"

    @pytest.mark.parametrize(
        "report",
        ["", "rust-toolchain.toml not found at /x/whitaker/rust-toolchain.toml\n"],
    )
    def test_a_report_without_the_labels_is_refused(self, report: str) -> None:
        """The pre-install failure the first design hit is an error, not a path."""
        assert _run("parse", stdin=report).returncode != 0, (
            "expected: _run('parse', stdin=report).returncode != 0"
        )

    def test_the_real_bootstrap_record_matches_the_pinned_row(self) -> None:
        """`bootstrap.json` carries the keys the verification step compares."""
        record = json.loads((FIXTURES / "bootstrap-0.2.11.json").read_text("utf-8"))
        row = (ACTION_DIR / "clippy-snapshots.tsv").read_text("utf-8")
        _, commit, dylint, toolchain = next(
            line.split() for line in row.splitlines() if line.startswith("0.2.11 ")
        )
        assert record["schema_version"] == 1, "expected: record['schema_version'] == 1"
        assert record["snapshot_commit"] == commit, (
            "expected: record['snapshot_commit'] == commit"
        )
        assert record["dylint_version"] == dylint, (
            "expected: record['dylint_version'] == dylint"
        )
        assert record["toolchain"] == toolchain, (
            "expected: record['toolchain'] == toolchain"
        )


class TestRoot:
    """The directory derived before the installer has run."""

    def test_linux_honours_xdg_data_home(self, tmp_path: Path) -> None:
        """`XDG_DATA_HOME` wins, as it does for the installer."""
        done = _run("root", "Linux", HOME="/home/runner", XDG_DATA_HOME=str(tmp_path))
        assert done.stdout.strip() == f"{tmp_path}/whitaker-bootstrap", (
            "expected: done.stdout.strip() == f'{tmp_path}/whitaker-bootstrap'"
        )

    def test_linux_falls_back_to_the_home_data_directory(self) -> None:
        """Without `XDG_DATA_HOME` the real report's directory is derived."""
        done = _run("root", "Linux", HOME="/home/runner", XDG_DATA_HOME="")
        report = (FIXTURES / "dry-run-0.2.11-installed.txt").read_text("utf-8")
        generation = _run("parse", stdin=report).stdout.splitlines()[0]
        assert generation.split("=", 1)[1].startswith(done.stdout.strip() + "/"), (
            "expected: generation.split('=', 1)[1].startswith(done.stdout.strip(..."
        )

    def test_macos_and_windows_follow_the_installers_project_directories(
        self,
    ) -> None:
        """The installer's `io.github.whitaker` project sits beside the bootstrap."""
        mac = _run("root", "macOS", HOME="/Users/runner")
        assert mac.stdout.strip() == (
            "/Users/runner/Library/Application Support/whitaker-bootstrap"
        ), "expected: mac.stdout.strip() == '/Users/runner/Library/Application "
        windows = _run("root", "Windows", HOME="/h", APPDATA="C:\\Users\\r\\AppData")
        assert windows.stdout.strip() == (
            "C:\\Users\\r\\AppData\\github\\whitaker\\whitaker-bootstrap"
        ), "expected: windows.stdout.strip() == 'C:UsersrAppDatagithubwhitakerw"

    def test_an_unknown_os_is_refused(self) -> None:
        """No guess is made for a platform the installer does not support."""
        assert _run("root", "Plan9", HOME="/h").returncode != 0, (
            "expected: _run('root', 'Plan9', HOME='/h').returncode != 0"
        )
