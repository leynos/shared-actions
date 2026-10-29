"""Exercise the pure Clippy-snapshot resolution script directly.

``scripts/resolve-snapshot.sh`` reads the installer version, the snapshot table
and the pinned bundle digests and prints a record. The cases state what it must
accept and, above all, what it must refuse: an unlisted version, an unpinned
digest, a conflicting digest, and an unreadable table or manifest each fail
closed, because the driver build has no other source for Clippy.
"""

from __future__ import annotations

import subprocess
import typing as typ

import pytest
from _action_manifest import ACTION_DIR

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
