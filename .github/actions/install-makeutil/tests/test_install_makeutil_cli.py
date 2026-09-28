"""The `resolve`/`install` CLI: dispatch, and publishing to a given env.

`main` takes an environment mapping as a parameter rather than reading
`os.environ` itself, so these tests build one in a temporary directory and
never call `monkeypatch.setenv` or mutate the process environment.

The `install` subcommand's download path is exercised at the
`install_makeutil` function level in `test_install_makeutil_install.py`,
where a downloader can be injected. Its default parameter binds the real,
network-using `default_downloader`, which the CLI layer has no seam to
replace without a live HTTPS server, so only its cache-hit path - which
never downloads - is tested here.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import install_makeutil as cli
import pytest
from makeutil_verify import CACHED, sha256_hex


def _fake_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment mapping with fresh, empty output files."""
    output_path = tmp_path / "github_output.txt"
    summary_path = tmp_path / "github_step_summary.txt"
    output_path.write_text("")
    summary_path.write_text("")
    return {
        "GITHUB_OUTPUT": str(output_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }


@dataclasses.dataclass(frozen=True)
class RefusedResolve:
    """One refused `resolve` invocation and the metric it must report."""

    version: str
    runner_os: str
    runner_arch: str
    expected_metric: str


def _outputs(env: dict[str, str]) -> dict[str, str]:
    """Parse the `key=value` lines `GITHUB_OUTPUT` accumulated."""
    lines = Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line)


class TestResolveSubcommand:
    """`resolve` publishes the install plan, or refuses closed."""

    def test_a_supported_platform_publishes_the_full_plan(self, tmp_path: Path) -> None:
        """Every output the `install` subcommand needs is present."""
        env = _fake_env(tmp_path)
        bin_dir = tmp_path / "bin"

        exit_code = cli.main(
            [
                "resolve",
                "--version",
                "0.1.0",
                "--bin-dir",
                str(bin_dir),
                "--runner-os",
                "Linux",
                "--runner-arch",
                "X64",
            ],
            env,
        )

        assert exit_code == 0
        outputs = _outputs(env)
        assert outputs["target"] == "x86_64-unknown-linux-musl"
        assert outputs["bin-dir"] == str(bin_dir.resolve())
        assert outputs["executable-path"] == str(bin_dir.resolve() / "makeutil")
        assert outputs["expected-sha256"] == (
            "99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d"
        )
        assert outputs["cache-key"] == (
            "install-makeutil-0.1.0-x86_64-unknown-linux-musl-"
            "99dd28a138dbe07e88e4dc5dd3954e6b29b46cc959635311d326cb537253115d"
        )

    def test_a_sha256_override_replaces_the_table_digest_in_the_output(
        self, tmp_path: Path
    ) -> None:
        """The override changes `expected-sha256` but not the cache key."""
        env = _fake_env(tmp_path)
        bin_dir = tmp_path / "bin"
        override = "1" * 64

        cli.main(
            [
                "resolve",
                "--version",
                "0.1.0",
                "--bin-dir",
                str(bin_dir),
                "--sha256-override",
                override,
                "--runner-os",
                "Linux",
                "--runner-arch",
                "X64",
            ],
            env,
        )

        outputs = _outputs(env)
        assert outputs["expected-sha256"] == override
        assert "99dd28a1" in outputs["cache-key"]

    @pytest.mark.parametrize(
        "refusal",
        [
            pytest.param(
                RefusedResolve("0.1.0", "Windows", "X64", "unsupported-platform"),
                id="unsupported-platform",
            ),
            pytest.param(
                RefusedResolve("0.0.1", "Linux", "X64", "unknown-version"),
                id="unknown-version",
            ),
            pytest.param(
                RefusedResolve("not-a-version", "Linux", "X64", "invalid-input"),
                id="malformed-version",
            ),
        ],
    )
    def test_a_refused_resolve_reports_its_bounded_metric(
        self, tmp_path: Path, refusal: RefusedResolve
    ) -> None:
        """Each refusal stage - input, version, platform - exits 1 and
        reports exactly its own documented result value, never a traceback.
        Input validation runs before the version and platform checks, which
        is what the malformed-version case, run against a valid platform,
        proves.
        """
        env = _fake_env(tmp_path)

        exit_code = cli.main(
            [
                "resolve",
                "--version",
                refusal.version,
                "--bin-dir",
                str(tmp_path / "bin"),
                "--runner-os",
                refusal.runner_os,
                "--runner-arch",
                refusal.runner_arch,
            ],
            env,
        )

        assert exit_code == 1
        summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
        assert f"install-makeutil.result={refusal.expected_metric}" in summary


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
