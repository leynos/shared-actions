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
from _makeutil_cli import fake_env as _fake_env
from _makeutil_cli import outputs as _outputs


@dataclasses.dataclass(frozen=True)
class RefusedResolve:
    """One refused `resolve` invocation and the metric it must report."""

    version: str
    runner_os: str
    runner_arch: str
    expected_metric: str


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
        release = "https://github.com/leynos/makeutil/releases/download/v0.1.0"
        assert outputs["binary-url"] == f"{release}/makeutil-x86_64-unknown-linux-musl"
        assert outputs["sidecar-url"] == (
            f"{release}/makeutil-x86_64-unknown-linux-musl.sha256"
        )
        assert outputs["version"] == "0.1.0"
        assert set(outputs) == {
            "target",
            "bin-dir",
            "executable-path",
            "expected-sha256",
            "binary-url",
            "sidecar-url",
            "cache-key",
            "version",
        }

    def test_resolving_publishes_the_plan_without_creating_bin_dir(
        self, tmp_path: Path
    ) -> None:
        """`resolve` is the query half: a missing `bin-dir` is planned, not made.

        Creating it is the `install` step's job, at the point it writes the
        binary.
        """
        env = _fake_env(tmp_path)
        bin_dir = tmp_path / "not" / "yet" / "there"

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
        assert _outputs(env)["bin-dir"] == str(bin_dir.resolve())
        assert not bin_dir.exists()

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
        assert _outputs(env) == {"result": refusal.expected_metric}

    def test_a_malformed_sha256_override_is_refused_before_any_output(
        self, tmp_path: Path
    ) -> None:
        """A non-hex, wrong-length, or newline-carrying override is refused
        the same way a malformed version is, and no outputs are published.
        """
        env = _fake_env(tmp_path)

        exit_code = cli.main(
            [
                "resolve",
                "--version",
                "0.1.0",
                "--bin-dir",
                str(tmp_path / "bin"),
                "--sha256-override",
                "A" * 64,
                "--runner-os",
                "Linux",
                "--runner-arch",
                "X64",
            ],
            env,
        )

        assert exit_code == 1
        assert _outputs(env) == {"result": "invalid-input"}
        summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
        assert "install-makeutil.result=invalid-input" in summary

    def test_an_unresolvable_bin_dir_is_refused_at_the_command_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path-resolution failure is mapped by the command layer to
        `invalid-input` and a published `result`, before any plan is made.
        """
        env = _fake_env(tmp_path)

        def _failing_resolve(self: Path, *_args: object, **_kwargs: object) -> Path:
            message = "simulated resolution failure"
            raise OSError(message)

        monkeypatch.setattr(Path, "resolve", _failing_resolve)

        exit_code = cli.main(
            [
                "resolve",
                "--version",
                "0.1.0",
                "--bin-dir",
                "/opt/tools/bin",
                "--runner-os",
                "Linux",
                "--runner-arch",
                "X64",
            ],
            env,
        )

        assert exit_code == 1
        assert _outputs(env) == {"result": "invalid-input"}
