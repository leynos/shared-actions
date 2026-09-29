"""The `install` CLI subcommand: cache states, failures and publication.

Only paths that need no network run here: a cache hit never downloads, and a
plain-HTTP URL is refused by the default downloader before any request.
"""

from __future__ import annotations

from pathlib import Path

import install_makeutil as cli
import pytest
from _makeutil_cli import fake_env as _fake_env
from _makeutil_cli import outputs as _outputs
from makeutil_verify import CACHED, sha256_hex


class TestInstallSubcommand:
    """`install` runs the orchestrator and publishes its outputs."""

    def test_a_cached_binary_reports_cached_and_publishes_outputs(
        self, tmp_path: Path
    ) -> None:
        """A pre-populated, correctly-verified `bin-dir` needs no download."""
        env = _fake_env(tmp_path)
        target = tmp_path / "bin" / "makeutil"
        target.parent.mkdir(parents=True)
        binary = b"already installed makeutil\n"
        target.write_bytes(binary)
        digest = sha256_hex(binary)

        exit_code = cli.main(
            [
                "install",
                "--executable-path",
                str(target),
                "--expected-sha256",
                digest,
                "--binary-url",
                "https://example.invalid/unreachable",
                "--sidecar-url",
                "https://example.invalid/unreachable.sha256",
                "--version",
                "0.1.0",
            ],
            env,
        )

        assert exit_code == 0
        outputs = _outputs(env)
        assert outputs["path"] == str(target)
        assert outputs["version"] == "0.1.0"
        assert outputs["result"] == CACHED
        summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
        assert f"install-makeutil.result={CACHED}" in summary

    @pytest.mark.parametrize(
        ("cache_hit", "expected_state"),
        [("true", "hit"), ("", "hit"), ("false", "hit")],
    )
    def test_a_reused_binary_reports_a_cache_hit(
        self, tmp_path: Path, cache_hit: str, expected_state: str
    ) -> None:
        """A verified reuse is a hit however the cache step phrased its output."""
        env = _fake_env(tmp_path)
        target = tmp_path / "bin" / "makeutil"
        target.parent.mkdir(parents=True)
        binary = b"already installed makeutil\n"
        target.write_bytes(binary)

        cli.main(
            [
                "install",
                "--executable-path",
                str(target),
                "--expected-sha256",
                sha256_hex(binary),
                "--binary-url",
                "https://example.invalid/unreachable",
                "--sidecar-url",
                "https://example.invalid/unreachable.sha256",
                "--version",
                "0.1.0",
                "--cache-hit",
                cache_hit,
            ],
            env,
        )

        summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
        assert f"install-makeutil.cache={expected_state}" in summary

    @pytest.mark.parametrize(
        ("cache_hit", "expected_state"),
        [("true", "stale"), ("false", "miss"), ("", "miss")],
    )
    def test_a_failed_install_reports_one_result_and_no_success_outputs(
        self, tmp_path: Path, cache_hit: str, expected_state: str
    ) -> None:
        """A refused download exits 1, emits exactly one bounded result, names
        a stale entry apart from a plain miss, and publishes nothing.

        A plain-HTTP URL is refused by the default downloader before any
        request, so the failure needs no network.
        """
        env = _fake_env(tmp_path)

        exit_code = cli.main(
            [
                "install",
                "--executable-path",
                str(tmp_path / "bin" / "makeutil"),
                "--expected-sha256",
                "0" * 64,
                "--binary-url",
                "http://example.invalid/makeutil",
                "--sidecar-url",
                "http://example.invalid/makeutil.sha256",
                "--version",
                "0.1.0",
                "--cache-hit",
                cache_hit,
            ],
            env,
        )

        assert exit_code == 1
        assert _outputs(env) == {}
        lines = (
            Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8").splitlines()
        )
        assert [line for line in lines if ".result=" in line] == [
            "install-makeutil.result=download-failed"
        ]
        assert f"install-makeutil.cache={expected_state}" in lines

    @pytest.mark.parametrize(
        ("cache_hit", "remains"), [("true", False), ("false", True), ("", True)]
    )
    def test_a_rejected_restored_binary_is_removed_only_when_restored(
        self, tmp_path: Path, cache_hit: str, remains: object
    ) -> None:
        """After a failed install, a binary the cache step restored must not
        stay usable, while one that was already in `bin-dir` is left as found.
        """
        env = _fake_env(tmp_path)
        target = tmp_path / "bin" / "makeutil"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"stale restored bytes\n")

        exit_code = cli.main(
            [
                "install",
                "--executable-path",
                str(target),
                "--expected-sha256",
                "0" * 64,
                "--binary-url",
                "http://example.invalid/makeutil",
                "--sidecar-url",
                "http://example.invalid/makeutil.sha256",
                "--version",
                "0.1.0",
                "--cache-hit",
                cache_hit,
            ],
            env,
        )

        assert exit_code == 1
        assert target.exists() is remains

    def test_a_failure_writing_step_outputs_is_reported_not_raised(
        self, tmp_path: Path
    ) -> None:
        """`GITHUB_OUTPUT` is the action's only channel to the workflow; when
        it cannot be written, `main` exits 1 with an annotation, no traceback.
        """
        env = _fake_env(tmp_path)
        env["GITHUB_OUTPUT"] = str(tmp_path / "missing-dir" / "output.txt")

        exit_code = cli.main(
            [
                "resolve",
                "--version",
                "0.1.0",
                "--bin-dir",
                str(tmp_path / "bin"),
                "--runner-os",
                "Linux",
                "--runner-arch",
                "X64",
            ],
            env,
        )

        assert exit_code == 1
