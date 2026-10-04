"""Contracts for the two ways a job declines an inherited `RUSTC_WRAPPER`.

`setup-rust` exports the wrapper to the whole job, and two kinds of process
inherit it without being able to use it: a root lane run through `sudo -E`, and
nested cargo builds such as trybuild fixtures. The documented answers are an
empty `RUSTC_WRAPPER` (Cargo counts it as unset) set for the step, or placed
inside the `sudo` command after `-E`, and, for a job that declines it
altogether, a wrapper scoped to one command from the `sccache-path` output.

These tests run real Cargo against a stand-in wrapper that records each call, so
the documented patterns are proved to do what the guide says rather than
assumed from the guide. They run the patterns without `sudo` itself, which a
test host cannot assume: `sudo -E env RUSTC_WRAPPER= cmd` reaches `cmd` with
exactly the environment that `env RUSTC_WRAPPER= cmd` does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None or shutil.which("bash") is None,
    reason="needs cargo and bash",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a one-file crate and a wrapper that records every call."""
    crate = tmp_path / "crate"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.0.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            echo called >> "$WRAPPER_LOG"
            exec "$@"
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return crate


def _build(project: Path, script: str, *, inherited: Path) -> int:
    """Run `script` as a job step would, with `inherited` as the job wrapper.

    Returns how many times the wrapper was called.
    """
    log = project.parent / "wrapper.log"
    log.write_text("", encoding="utf-8")
    environment = {
        **os.environ,
        "RUSTC_WRAPPER": str(inherited),
        "WRAPPER_LOG": str(log),
        "CARGO_TARGET_DIR": str(project.parent / "target"),
    }
    environment.pop("RUSTC_WORKSPACE_WRAPPER", None)
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the documented pattern.
        ["bash", "-c", script],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=project,
        env=environment,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stderr
    return len(log.read_text(encoding="utf-8").splitlines())


class TestDecliningAnInheritedWrapper:
    """The patterns the guide documents each keep the wrapper out of a build."""

    def test_the_wrapper_is_used_when_inherited(self, project: Path) -> None:
        """The control: without a pattern the job-wide wrapper is in play."""
        wrapper = project.parent / "wrapper.sh"

        assert _build(project, "cargo build -q --offline", inherited=wrapper) > 0

    def test_an_empty_value_in_the_step_env_declines_it(self, project: Path) -> None:
        """The nested-cargo pattern: `env: RUSTC_WRAPPER: ''` on the step."""
        wrapper = project.parent / "wrapper.sh"

        assert (
            _build(
                project,
                "RUSTC_WRAPPER='' cargo build -q --offline",
                inherited=wrapper,
            )
            == 0
        )

    def test_an_empty_value_after_sudo_dash_e_declines_it(self, project: Path) -> None:
        """The root pattern: `sudo -E env RUSTC_WRAPPER= make test`.

        `sudo -E` preserves the environment and `env` then overrides one
        variable before the command runs, so `env RUSTC_WRAPPER= cmd` stands
        for it here.
        """
        wrapper = project.parent / "wrapper.sh"

        assert (
            _build(
                project,
                "env RUSTC_WRAPPER= cargo build -q --offline",
                inherited=wrapper,
            )
            == 0
        )

    def test_a_scoped_wrapper_is_used_only_where_set(self, project: Path) -> None:
        """A job with `export-rustc-wrapper: false` scopes it per command.

        The wrapper is named from the `sccache-path` output for one command and
        absent for the next, which is the point of declining it job-wide.
        """
        wrapper = project.parent / "wrapper.sh"
        log = project.parent / "wrapper.log"
        log.write_text("", encoding="utf-8")
        environment = {
            **os.environ,
            "WRAPPER_LOG": str(log),
            "CARGO_TARGET_DIR": str(project.parent / "target"),
        }
        environment.pop("RUSTC_WRAPPER", None)
        environment.pop("RUSTC_WORKSPACE_WRAPPER", None)

        def build(prefix: str) -> int:
            log.write_text("", encoding="utf-8")
            subprocess.run(  # noqa: S603,TID251 - exercise the documented pattern.
                ["bash", "-c", f"{prefix}cargo build -q --offline"],  # noqa: S607
                capture_output=True,
                check=True,
                cwd=project,
                env=environment,
                text=True,
                timeout=240,
            )
            return len(log.read_text(encoding="utf-8").splitlines())

        unscoped = build("")
        (project.parent / "target").mkdir(exist_ok=True)
        subprocess.run(  # noqa: S603,TID251 - reset the build between runs.
            [shutil.which("cargo") or "cargo", "clean", "-q"],
            check=True,
            cwd=project,
            env={**environment},
            timeout=240,
        )
        scoped = build(f"RUSTC_WRAPPER={wrapper} ")

        assert unscoped == 0
        assert scoped > 0


class TestTheGuideNamesBothPatterns:
    """The README is where a caller finds them, so it must say them exactly."""

    @pytest.mark.parametrize(
        "text",
        [
            "export-rustc-wrapper: 'false'",
            "RUSTC_WRAPPER: ''",
            "sudo -E env RUSTC_WRAPPER= make test",
            "sccache-path",
        ],
    )
    def test_the_readme_documents(self, text: str) -> None:
        """Each documented form appears verbatim."""
        assert text in README.read_text(encoding="utf-8")
