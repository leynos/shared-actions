"""Exercise the pure Clippy-snapshot resolution script directly.

``scripts/resolve-snapshot.sh`` reads the installer version, the snapshot table
and the pinned bundle digests and prints a record. The cases state what it must
accept and, above all, what it must refuse: an unlisted version, an unpinned
digest, a conflicting digest, and an unreadable table or manifest each fail
closed, because the driver build has no other source for Clippy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ

import pytest
from _action_manifest import ACTION_DIR
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from composite_fragments import ambient_env, bash_executable, require_posix_host

if typ.TYPE_CHECKING:
    from pathlib import Path

SCRIPT = ACTION_DIR / "scripts" / "resolve-snapshot.sh"
TABLE = ACTION_DIR / "clippy-snapshots.tsv"
MANIFEST = ACTION_DIR / "snapshot-digests.sha256"
COMMIT = "e4608a25c2e20116fbc1e97a51a73f094a1db7a1"
BUNDLE = f"whitaker-clippy-snapshot-{COMMIT}.bundle"
PINNED = "a" * 64
SUPPLIED = "b" * 64

require_posix_host()


def _resolve(tmp_path: Path, version: str, **overrides: str) -> dict[str, str]:
    """Run the script and parse its `key=value` record."""
    manifest = tmp_path / "snapshot-digests.sha256"
    manifest.write_text(f"{PINNED}  {BUNDLE}\n", encoding="utf-8")
    env = {
        **ambient_env(),
        "WHITAKER_INSTALLER_VERSION": version,
        "WHITAKER_SNAPSHOT_TABLE": str(TABLE),
        "WHITAKER_SNAPSHOT_DIGEST_MANIFEST": str(manifest),
        "WHITAKER_SNAPSHOT_SHA256": "",
        **overrides,
    }
    done = subprocess.run(  # noqa: S603,TID251 - run the script under test.
        [bash_executable(), str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)


class TestResolveSnapshot:
    """The record for each way a snapshot can, or cannot, be resolved."""

    @pytest.mark.parametrize("version", ["0.2.9", "0.2.10", "0.2.8"])
    def test_an_installer_before_the_bootstrap_has_no_snapshot(
        self, tmp_path: Path, version: str
    ) -> None:
        """Installers older than 0.2.11 take no snapshot and are not refused."""
        assert _resolve(tmp_path, version) == {"status": "none"}

    def test_the_first_bootstrap_installer_resolves_to_its_pinned_bundle(
        self, tmp_path: Path
    ) -> None:
        """The record names the bundle, commit and digest and says it is pinned."""
        record = _resolve(tmp_path, "0.2.11")
        assert record == {
            "status": "install",
            "bundle-asset": BUNDLE,
            "sidecar-asset": f"{BUNDLE}.sha256",
            "commit": COMMIT,
            "dylint-version": "6.3.0",
            "toolchain": "nightly-2026-05-28",
            "expected-sha": PINNED,
            "trust-anchor": "pinned",
        }

    def test_a_supplied_digest_serves_an_unpinned_bundle(self, tmp_path: Path) -> None:
        """Without a pin the caller's digest is the anchor, and says so."""
        empty = tmp_path / "empty.sha256"
        empty.write_text("", encoding="utf-8")
        record = _resolve(
            tmp_path,
            "0.2.11",
            WHITAKER_SNAPSHOT_DIGEST_MANIFEST=str(empty),
            WHITAKER_SNAPSHOT_SHA256=SUPPLIED,
        )
        assert record["expected-sha"] == SUPPLIED
        assert record["trust-anchor"] == "input"

    @pytest.mark.parametrize(
        ("version", "overrides", "kind"),
        [
            pytest.param("0.2.12", {}, "unlisted-version", id="unlisted-version"),
            pytest.param(
                "0.2.11",
                {"WHITAKER_SNAPSHOT_SHA256": SUPPLIED},
                "digest-conflict",
                id="conflicting-digest",
            ),
            pytest.param(
                "0.2.11",
                {"WHITAKER_SNAPSHOT_TABLE": "/nonexistent/table"},
                "table-unreadable",
                id="missing-table",
            ),
            pytest.param(
                "0.2.11",
                {"WHITAKER_SNAPSHOT_DIGEST_MANIFEST": "/nonexistent/manifest"},
                "manifest-unreadable",
                id="missing-manifest",
            ),
        ],
    )
    def test_every_gap_fails_closed(
        self, tmp_path: Path, version: str, overrides: dict[str, str], kind: str
    ) -> None:
        """A gap in the trust chain is an error record, never a degraded install."""
        record = _resolve(tmp_path, version, **overrides)
        assert record["status"] == "error", record
        assert record["error-kind"] == kind, record

    def test_an_unpinned_digest_with_no_input_fails_closed(
        self, tmp_path: Path
    ) -> None:
        """No pin and no supplied digest leaves nothing to verify against."""
        empty = tmp_path / "empty.sha256"
        empty.write_text("", encoding="utf-8")
        record = _resolve(
            tmp_path, "0.2.11", WHITAKER_SNAPSHOT_DIGEST_MANIFEST=str(empty)
        )
        assert record["status"] == "error", record
        assert record["error-kind"] == "unpinned-digest", record

    @pytest.mark.parametrize(
        ("failing", "kind"),
        [("table", "table-unreadable"), ("manifest", "manifest-unreadable")],
    )
    def test_a_failed_read_is_an_error_not_an_empty_answer(
        self, tmp_path: Path, failing: str, kind: str
    ) -> None:
        """An `awk` that fails reading the table or manifest is not a miss.

        Folding the failure into "no such row" or "no pinned digest" would turn
        an I/O fault into `unlisted-version`, or into acceptance of whatever
        digest the caller supplied.
        """
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        marker = "snapshot-digests" if failing == "manifest" else "clippy-snapshots"
        real_awk = shutil.which("awk")
        assert real_awk is not None, "the host has no awk to wrap"
        awk = stubs / "awk"
        awk.write_text(
            "#!/usr/bin/env bash\n"
            f'for arg in "$@"; do case "$arg" in *{marker}*) exit 2;; esac; done\n'
            f'exec "{real_awk}" "$@"\n',
            encoding="utf-8",
        )
        awk.chmod(0o755)
        record = _resolve(
            tmp_path,
            "0.2.11",
            PATH=f"{stubs}:{os.environ['PATH']}",
            WHITAKER_SNAPSHOT_SHA256=SUPPLIED,
        )
        assert record["status"] == "error", record
        assert record["error-kind"] == kind, record

    def test_the_shipped_manifest_pins_every_table_row_or_the_action_refuses(
        self,
    ) -> None:
        """Each tabled version either has a pinned bundle digest or fails closed.

        Until a row is pinned from the published asset, the resolution of that
        version is an `unpinned-digest` error, so the default cannot silently
        install an unverified bundle.
        """
        pinned = {
            line.split()[1]
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        for line in TABLE.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            version, commit, *_ = line.split()
            bundle = f"whitaker-clippy-snapshot-{commit}.bundle"
            assert bundle in pinned, f"{version}: {bundle} has no pinned digest"


class TestResolutionIsPure:
    """The resolver is a query: it prints a record and changes nothing."""

    def test_it_writes_no_file_output_summary_or_network_call(
        self, tmp_path: Path
    ) -> None:
        """A run leaves the tree, the workflow files and the network alone.

        `GITHUB_OUTPUT` and `GITHUB_STEP_SUMMARY` point at empty files that
        must stay empty, no annotation reaches either stream, every
        network-capable command is a stub that records a call, and the working
        tree is byte for byte what it was.
        """
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        calls = tmp_path / "calls.log"
        for name in ("curl", "wget", "git", "gh", "ssh", "nc"):
            stub = stubs / name
            stub.write_text(
                f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\nexit 99\n',
                encoding="utf-8",
            )
            stub.chmod(0o755)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "keep.txt").write_text("unchanged", encoding="utf-8")
        output = tmp_path / "github_output"
        summary = tmp_path / "github_summary"
        output.write_text("", encoding="utf-8")
        summary.write_text("", encoding="utf-8")
        manifest = tmp_path / "snapshot-digests.sha256"
        manifest.write_text(f"{PINNED}  {BUNDLE}\n", encoding="utf-8")
        before = sorted(
            (path.relative_to(tmp_path), path.read_bytes())
            for path in tmp_path.rglob("*")
            if path.is_file() and path != calls
        )
        done = subprocess.run(  # noqa: S603,TID251 - run the script under test.
            [bash_executable(), str(SCRIPT)],
            env={
                **ambient_env(),
                "PATH": f"{stubs}:{os.environ['PATH']}",
                "WHITAKER_INSTALLER_VERSION": "0.2.11",
                "WHITAKER_SNAPSHOT_TABLE": str(TABLE),
                "WHITAKER_SNAPSHOT_DIGEST_MANIFEST": str(manifest),
                "WHITAKER_SNAPSHOT_SHA256": "",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
        after = sorted(
            (path.relative_to(tmp_path), path.read_bytes())
            for path in tmp_path.rglob("*")
            if path.is_file() and path != calls
        )
        assert done.returncode == 0, done.stderr
        assert "status=install" in done.stdout
        assert after == before, "the resolver changed a file"
        assert output.read_text("utf-8") == "", "the resolver wrote GITHUB_OUTPUT"
        assert summary.read_text("utf-8") == "", "the resolver wrote the summary"
        assert "::" not in done.stdout + done.stderr, (
            "the resolver emitted an annotation"
        )
        assert not calls.exists(), f"the resolver called the network: {calls}"


_PROPERTY = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class TestVersionThreshold:
    """The `none` to `install` transition, over generated versions."""

    @_PROPERTY
    @given(
        major=st.just(0),
        minor=st.integers(min_value=0, max_value=1),
        patch=st.integers(min_value=0, max_value=99),
    )
    def test_every_version_before_0_2_0_has_no_bootstrap(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        major: int,
        minor: int,
        patch: int,
    ) -> None:
        """Anything below 0.2.0 takes no snapshot and is not refused."""
        version = f"{major}.{minor}.{patch}"
        assert _resolve(tmp_path_factory.mktemp("old"), version) == {"status": "none"}

    @_PROPERTY
    @given(patch=st.integers(min_value=0, max_value=10))
    def test_every_0_2_patch_below_11_has_no_bootstrap(
        self, tmp_path_factory: pytest.TempPathFactory, patch: int
    ) -> None:
        """The threshold is exactly 0.2.11."""
        record = _resolve(tmp_path_factory.mktemp("below"), f"0.2.{patch}")
        assert record == {"status": "none"}

    @_PROPERTY
    @given(
        version=st.sampled_from(
            ["0.2.11", "0.2.12", "0.2.99", "0.3.0", "0.10.0", "1.0.0", "2.4.1"]
        )
    )
    def test_every_later_version_resolves_to_install_or_a_closed_error(
        self, tmp_path_factory: pytest.TempPathFactory, version: str
    ) -> None:
        """A bootstrap-era version is installed if tabled and refused if not."""
        record = _resolve(tmp_path_factory.mktemp("later"), version)
        if version == "0.2.11":
            assert record["status"] == "install"
        else:
            assert record["status"] == "error", record
            assert record["error-kind"] == "unlisted-version", record
