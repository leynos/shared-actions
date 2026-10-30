"""Run the bootstrap steps' Bash fragments against controlled fixtures.

``test_install_whitaker_bootstrap.py`` holds the shape of the steps; these
cases execute the shipped fragments, so an implementation that accepts a
corrupted download, trusts stale cache metadata, or turns the final
verification into a no-op fails here. The network, the installer and the
filesystem are replaced by stubs on ``PATH`` and files under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import typing as typ

import pytest
from _action_manifest import ACTION_DIR, step_by_id

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    ambient_env,
    require_posix_host,
    run_step,
)

if typ.TYPE_CHECKING:
    import subprocess
    from pathlib import Path

require_posix_host()

COMMIT = "e4608a25c2e20116fbc1e97a51a73f094a1db7a1"
DYLINT = "6.3.0"
TOOLCHAIN = "nightly-2026-05-28"
BUNDLE = f"whitaker-clippy-snapshot-{COMMIT}.bundle"
SIDECAR = f"{BUNDLE}.sha256"
PAYLOAD = b"a bundle of Clippy history"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()

_RECORD: typ.Final[dict[str, object]] = {
    "schema_version": 1,
    "snapshot_commit": COMMIT,
    "dylint_version": DYLINT,
    "toolchain": TOOLCHAIN,
}


@dc.dataclass(frozen=True)
class Outcome:
    """The result of one fragment run."""

    process: subprocess.CompletedProcess[str]
    outputs: dict[str, str]

    @property
    def log(self) -> str:
        """Everything the fragment printed, for message assertions."""
        return self.process.stdout + self.process.stderr


def _run(
    tmp_path: Path,
    step_id: str,
    step_outputs: dict[str, dict[str, str]],
    extra_env: dict[str, str] | None = None,
) -> Outcome:
    """Run the fragment of ``step_id`` with the given upstream step outputs."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    context = ActionContext(
        inputs={},
        runner_os="Linux",
        runner_arch="X64",
        action_path=str(ACTION_DIR),
        runner_temp=str(temp),
        step_outputs=step_outputs,
    )
    process = run_step(
        step_by_id(step_id),
        context,
        FragmentEnvironment(
            base_env={
                **ambient_env(),
                "HOME": str(home),
                "RUNNER_OS": "Linux",
                "RUNNER_TEMP": str(temp),
                **(extra_env or {}),
            },
            cwd=tmp_path,
            output_dir=tmp_path / "out",
        ),
        step_id,
    )
    outputs: dict[str, str] = {}
    produced = tmp_path / "out" / step_id
    if produced.exists():
        outputs = dict(
            line.split("=", 1)
            for line in produced.read_text("utf-8").splitlines()
            if "=" in line
        )
    return Outcome(process, outputs)


def _stub(directory: Path, name: str, body: str) -> None:
    """Write an executable ``name`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class TestBootstrapCacheVerification:
    """A restored bootstrap is trusted only when it matches the pins."""

    @staticmethod
    def _step(tmp_path: Path, root: Path) -> Outcome:
        """Run the cache-verification fragment against a bootstrap at ``root``."""
        return _run(
            tmp_path,
            "bootstrap-cache",
            {
                "bootstrap-location": {"root": str(root)},
                "snapshot": {
                    "commit": COMMIT,
                    "dylint-version": DYLINT,
                    "toolchain": TOOLCHAIN,
                },
            },
        )

    @staticmethod
    def _populate(
        root: Path,
        record: dict[str, object] | str | None = None,
        *,
        generation: bool = True,
    ) -> Path:
        """Lay out a bootstrap and a neighbour that must never be touched."""
        root.mkdir(parents=True)
        if record is None:
            record = _RECORD
        if isinstance(record, str):
            (root / "bootstrap.json").write_text(record, encoding="utf-8")
        elif record != {}:
            (root / "bootstrap.json").write_text(json.dumps(record), encoding="utf-8")
        if generation:
            (root / "clippy" / COMMIT).mkdir(parents=True)
        neighbour = root.parent / "keep" / "installer"
        neighbour.parent.mkdir()
        neighbour.write_text("installer", encoding="utf-8")
        return neighbour

    def test_an_absent_bootstrap_is_empty(self, tmp_path: Path) -> None:
        """Nothing restored reports `empty` and asks for the download."""
        outcome = self._step(tmp_path, tmp_path / "data" / "whitaker-bootstrap")
        assert outcome.process.returncode == 0, outcome.log
        assert outcome.outputs == {"state": "empty", "snapshot-installed": "false"}

    def test_a_matching_bootstrap_is_kept(self, tmp_path: Path) -> None:
        """Metadata and generation that agree with the pins are restored."""
        root = tmp_path / "data" / "whitaker-bootstrap"
        neighbour = self._populate(root)
        outcome = self._step(tmp_path, root)
        assert outcome.process.returncode == 0, outcome.log
        assert outcome.outputs == {"state": "restored", "snapshot-installed": "true"}
        assert (root / "bootstrap.json").exists()
        assert neighbour.exists()

    @pytest.mark.parametrize(
        ("record", "generation", "reason"),
        [
            ({**_RECORD, "snapshot_commit": "0" * 40}, True, "does not match"),
            ({**_RECORD, "dylint_version": "6.0.1"}, True, "does not match"),
            ({**_RECORD, "toolchain": "nightly-2020-01-01"}, True, "does not match"),
            ({**_RECORD, "schema_version": 2}, True, "does not match"),
            ({"schema_version": 1}, True, "does not match"),
            ("{not json", True, "does not match"),
            ({}, True, "bootstrap.json is missing"),
            (_RECORD, False, "snapshot generation is missing"),
        ],
        ids=[
            "stale-commit",
            "stale-dylint",
            "stale-toolchain",
            "schema",
            "incomplete",
            "malformed",
            "no-metadata",
            "no-generation",
        ],
    )
    def test_stale_malformed_or_incomplete_metadata_wipes_only_the_bootstrap(
        self,
        tmp_path: Path,
        record: dict[str, object] | str,
        generation: bool,  # noqa: FBT001 - a parametrised flag.
        reason: str,
    ) -> None:
        """The bootstrap alone is removed, with the reason, and reported `wiped`."""
        root = tmp_path / "data" / "whitaker-bootstrap"
        neighbour = self._populate(root, record, generation=generation)
        outcome = self._step(tmp_path, root)
        assert outcome.process.returncode == 0, outcome.log
        assert outcome.outputs == {"state": "wiped", "snapshot-installed": "false"}
        assert "::warning" in outcome.log
        assert reason in outcome.log
        assert not root.exists()
        assert neighbour.exists(), "the wipe reached beyond the bootstrap directory"

    @pytest.mark.parametrize("name", ["data", "whitaker", "whitaker-bootstrap-old"])
    def test_a_root_that_is_not_the_bootstrap_directory_is_never_wiped(
        self, tmp_path: Path, name: str
    ) -> None:
        """A mislocated root is refused before anything is removed."""
        root = tmp_path / name
        neighbour = self._populate(root, {"schema_version": 9})
        outcome = self._step(tmp_path, root)
        assert outcome.process.returncode != 0
        assert "refusing to manage" in outcome.log
        assert root.exists()
        assert neighbour.exists()


class TestSnapshotDownload:
    """The bundle is trusted only after two digests agree."""

    @staticmethod
    def _step(
        tmp_path: Path,
        *,
        bundle: bytes = PAYLOAD,
        sidecar: str | None = None,
        expected: str = PAYLOAD_SHA256,
        curl_fails: bool = False,
    ) -> Outcome:
        """Run the download fragment against a stubbed release and ``curl``."""
        if sidecar is None:
            sidecar = f"{PAYLOAD_SHA256}  {BUNDLE}\n"
        release = tmp_path / "release"
        release.mkdir()
        (release / BUNDLE).write_bytes(bundle)
        (release / SIDECAR).write_text(sidecar, encoding="utf-8")
        stubs = tmp_path / "stubs"
        _stub(
            stubs,
            "curl",
            'if [[ "${1:-}" == --version ]]; then\n'
            '  echo "curl 8.0.0 (stub)"; exit 0\n'
            "fi\n"
            'if [[ "$CURL_FAILS" == 1 ]]; then exit 22; fi\n'
            'url=""; out=""\n'
            'while (( $# )); do case "$1" in -o) out="$2"; shift;; '
            'http*) url="$1";; esac; shift; done\n'
            'cp -- "$RELEASE_DIR/${url##*/}" "$out"',
        )
        return _run(
            tmp_path,
            "snapshot-download",
            {
                "snapshot": {
                    "bundle-asset": BUNDLE,
                    "sidecar-asset": SIDECAR,
                    "expected-sha": expected,
                    "trust-anchor": "pinned",
                },
                "validate-inputs": {"installer-version": "0.2.11"},
            },
            {
                "PATH": f"{stubs}:{ambient_env()['PATH']}",
                "RELEASE_DIR": str(release),
                "CURL_FAILS": "1" if curl_fails else "0",
            },
        )

    def test_agreeing_digests_publish_the_staged_bundle(self, tmp_path: Path) -> None:
        """A bundle matching the pin and its sidecar is handed on."""
        outcome = self._step(tmp_path)
        assert outcome.process.returncode == 0, outcome.log
        assert outcome.outputs["bundle"].endswith(f"/whitaker-clippy-snapshot/{BUNDLE}")

    def test_a_corrupted_bundle_fails_the_pinned_digest(self, tmp_path: Path) -> None:
        """Bytes that differ from the pin are refused, sidecar or no sidecar."""
        outcome = self._step(tmp_path, bundle=b"tampered")
        assert outcome.process.returncode != 0
        assert "snapshot digest mismatch" in outcome.log
        assert "bundle" not in outcome.outputs

    def test_a_disagreeing_sidecar_fails_closed(self, tmp_path: Path) -> None:
        """A sidecar naming another digest refuses a bundle that matches the pin."""
        outcome = self._step(tmp_path, sidecar=f"{'c' * 64}  {BUNDLE}\n")
        assert outcome.process.returncode != 0
        assert "sidecar digest" in outcome.log
        assert "bundle" not in outcome.outputs

    def test_an_empty_sidecar_fails_closed(self, tmp_path: Path) -> None:
        """A sidecar with no digest never counts as agreement."""
        outcome = self._step(tmp_path, sidecar="")
        assert outcome.process.returncode != 0
        assert "sidecar digest <empty>" in outcome.log

    def test_a_supplied_digest_that_disagrees_with_the_bundle_fails(
        self, tmp_path: Path
    ) -> None:
        """The expected digest, pinned or supplied, is the one the bytes meet."""
        outcome = self._step(tmp_path, expected="d" * 64)
        assert outcome.process.returncode != 0
        assert "snapshot digest mismatch" in outcome.log

    def test_each_transfer_is_reported_with_its_part_and_outcome(
        self, tmp_path: Path
    ) -> None:
        """Both assets report a bounded transfer notice, as the installer does."""
        outcome = self._step(tmp_path)
        assert (
            "Whitaker Clippy snapshot transfer::part=bundle outcome=ok" in outcome.log
        )
        assert "part=sidecar outcome=ok" in outcome.log

    def test_a_failed_download_is_reported_as_a_failed_transfer(
        self, tmp_path: Path
    ) -> None:
        """A failure carries the failed outcome before the step error."""
        outcome = self._step(tmp_path, curl_fails=True)
        assert "part=bundle outcome=failed" in outcome.log

    def test_a_failed_download_names_the_asset_and_does_not_fall_back(
        self, tmp_path: Path
    ) -> None:
        """No download means no bundle, and no other source is tried."""
        outcome = self._step(tmp_path, curl_fails=True)
        assert outcome.process.returncode != 0
        assert f"could not download {BUNDLE}" in outcome.log
        assert "bundle" not in outcome.outputs


class TestFinalVerification:
    """What the installer left behind must be the pinned bootstrap."""

    @staticmethod
    def _layout(tmp_path: Path) -> dict[str, Path]:
        """Lay out an installed bootstrap, its wrapper and a stub bin directory."""
        root = tmp_path / "data" / "whitaker-bootstrap"
        generation = root / "clippy" / COMMIT
        driver = root / "drivers" / f"dylint-{DYLINT}" / f"clippy-{COMMIT}"
        generation.mkdir(parents=True)
        (root / "bootstrap.json").write_text(json.dumps(_RECORD), encoding="utf-8")
        wrapper = tmp_path / "home" / ".local" / "bin"
        _stub(wrapper, "whitaker", "exit 0")
        cargo_bin = tmp_path / "cargo" / "bin"
        return {
            "root": root,
            "generation": generation,
            "driver": driver,
            "cargo_bin": cargo_bin,
        }

    @staticmethod
    def _step(
        tmp_path: Path,
        layout: dict[str, Path],
        *,
        report: str | None = None,
        dry_run_status: int = 0,
    ) -> Outcome:
        """Run the final fragment against a stubbed installer and its report."""
        if report is None:
            report = (
                f"Dry run - no files will be modified\n\n"
                f"Clippy snapshot: {layout['generation']}\n"
                f"Clippy snapshot state: installed\n"
                f"Dylint driver directory: {layout['driver']}\n"
            )
        _stub(
            layout["cargo_bin"],
            "whitaker-installer",
            f"cat <<'REPORT'\n{report}\nREPORT\nexit {dry_run_status}",
        )
        return _run(
            tmp_path,
            "bootstrap",
            {
                "bootstrap-location": {
                    "root": str(layout["root"]),
                    "generation": str(layout["generation"]),
                    "driver-directory": str(layout["driver"]),
                },
                "snapshot": {
                    "commit": COMMIT,
                    "dylint-version": DYLINT,
                    "toolchain": TOOLCHAIN,
                },
                "validate-inputs": {
                    "installer-path": str(layout["cargo_bin"] / "whitaker-installer"),
                },
            },
        )

    def test_a_faithful_install_passes(self, tmp_path: Path) -> None:
        """Right metadata, right paths, a wrapper in place: the step succeeds."""
        outcome = self._step(tmp_path, self._layout(tmp_path))
        assert outcome.process.returncode == 0, outcome.log

    @pytest.mark.parametrize(
        "record",
        [
            {**_RECORD, "snapshot_commit": "1" * 40},
            {**_RECORD, "dylint_version": "6.0.1"},
            {**_RECORD, "toolchain": "nightly-2020-01-01"},
            {**_RECORD, "schema_version": 2},
        ],
        ids=["commit", "dylint", "toolchain", "schema"],
    )
    def test_incorrect_bootstrap_metadata_fails(
        self, tmp_path: Path, record: dict[str, object]
    ) -> None:
        """Metadata that disagrees with any pin fails the step."""
        layout = self._layout(tmp_path)
        (layout["root"] / "bootstrap.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        outcome = self._step(tmp_path, layout)
        assert outcome.process.returncode != 0
        assert "bootstrap.json does not match" in outcome.log

    def test_unreadable_bootstrap_metadata_fails(self, tmp_path: Path) -> None:
        """Metadata that is not JSON fails the step rather than passing it."""
        layout = self._layout(tmp_path)
        (layout["root"] / "bootstrap.json").write_text("{nope", encoding="utf-8")
        assert self._step(tmp_path, layout).process.returncode != 0

    def test_a_missing_snapshot_generation_fails(self, tmp_path: Path) -> None:
        """No snapshot directory means the driver build has no Clippy."""
        layout = self._layout(tmp_path)
        layout["generation"].rmdir()
        outcome = self._step(tmp_path, layout)
        assert outcome.process.returncode != 0
        assert "no Clippy snapshot" in outcome.log

    def test_a_reported_snapshot_elsewhere_fails(self, tmp_path: Path) -> None:
        """The installer's own report must name the derived generation."""
        layout = self._layout(tmp_path)
        report = (
            f"Clippy snapshot: {tmp_path}/elsewhere/clippy/{COMMIT}\n"
            f"Clippy snapshot state: installed\n"
            f"Dylint driver directory: {layout['driver']}\n"
        )
        outcome = self._step(tmp_path, layout, report=report)
        assert outcome.process.returncode != 0
        assert "reports its snapshot at" in outcome.log

    def test_a_reported_driver_directory_elsewhere_fails(self, tmp_path: Path) -> None:
        """The installer's own report must name the derived driver directory."""
        layout = self._layout(tmp_path)
        report = (
            f"Clippy snapshot: {layout['generation']}\n"
            f"Clippy snapshot state: installed\n"
            f"Dylint driver directory: {tmp_path}/elsewhere/drivers\n"
        )
        outcome = self._step(tmp_path, layout, report=report)
        assert outcome.process.returncode != 0
        assert "reports its driver directory at" in outcome.log

    def test_a_failed_dry_run_with_a_plausible_report_fails(
        self, tmp_path: Path
    ) -> None:
        """A non-zero dry run is an error even when its output parses."""
        layout = self._layout(tmp_path)
        outcome = self._step(tmp_path, layout, dry_run_status=3)
        assert outcome.process.returncode != 0
        assert "dry run failed" in outcome.log

    def test_a_report_without_the_labels_fails(self, tmp_path: Path) -> None:
        """Partial output that names no snapshot or driver is refused."""
        layout = self._layout(tmp_path)
        outcome = self._step(tmp_path, layout, report="Workspace root: /x")
        assert outcome.process.returncode != 0
        assert "could not read the installer's dry-run report" in outcome.log

    def test_a_missing_wrapper_fails(self, tmp_path: Path) -> None:
        """Without the `whitaker` wrapper the driver gets no bootstrap."""
        layout = self._layout(tmp_path)
        (tmp_path / "home" / ".local" / "bin" / "whitaker").unlink()
        outcome = self._step(tmp_path, layout)
        assert outcome.process.returncode != 0
        assert "wrapper is not in" in outcome.log
