"""Tests for the sccache directory cache `setup-rust` owns on hosted runners.

On a GitHub-hosted runner left to the action, sccache writes to a local
directory, and the action restores and saves that directory itself. Three
things have to hold. The key is one lane's: OS, architecture, compiler, a
discriminator, then the lockfile. Only a push to the default branch saves, so
the trunk is the single writer and a pull request never seeds another branch's
cache. And none of it runs unless the selection step said the action owns the
directory, which it does not when the caller chose a backend or a directory.
"""

from __future__ import annotations

import os
import stat
import subprocess
import typing as typ

import pytest
from setup_rust_test_helpers import get_step, load_steps, requires_bash

if typ.TYPE_CHECKING:
    from pathlib import Path

KEY_STEP = "Key the sccache directory cache"
TRUNK_STEP = "Restore and save the sccache directory on the default branch"
RESTORE_STEP = "Restore the sccache directory"
SERVER_STEP = "Start the sccache server"
BACKEND_STEP = "Select the sccache backend"

CACHE_REVISION = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
OWNS = "steps.sccache-backend.outputs.owns-local-cache == 'true'"
TRUNK_PUSH = (
    "github.event_name == 'push' && github.ref == "
    "format('refs/heads/{0}', github.event.repository.default_branch)"
)


def _normalized(expression: object) -> str:
    """Return *expression* with its whitespace collapsed to single spaces."""
    return " ".join(str(expression).split())


class TestPlacement:
    """Where the cache steps sit, and which one may write."""

    def test_they_follow_the_selection_and_precede_the_server(self) -> None:
        """They need the selection's outputs, and the server needs the files.

        A directory restored after the server started would still be read,
        but a restore after the caller's build would be read by nothing.
        """
        names = [step.get("name") for step in load_steps()]

        for name in (KEY_STEP, TRUNK_STEP, RESTORE_STEP):
            assert names.index(BACKEND_STEP) < names.index(name), name
            assert names.index(name) < names.index(SERVER_STEP), name

    def test_the_key_is_derived_only_when_the_action_owns_the_directory(
        self,
    ) -> None:
        """A caller's directory, or GitHub's service, is not the action's."""
        assert _normalized(get_step(KEY_STEP)["if"]) == f"${{{{ {OWNS} }}}}", (
            "the key step must run exactly when the action owns the directory"
        )

    def test_only_a_trunk_push_can_save(self) -> None:
        """The full action saves in its post step; it runs on the trunk only.

        Asserted as the whole predicate, so a dropped ref check, which would
        let every branch push write, fails here.
        """
        step = get_step(TRUNK_STEP)

        assert step["uses"] == f"actions/cache@{CACHE_REVISION}", step["uses"]
        assert _normalized(step["if"]) == f"${{{{ {OWNS} && {TRUNK_PUSH} }}}}", (
            f"the saving step must be gated on a trunk push, not {step['if']!r}"
        )

    def test_everything_else_only_restores(self) -> None:
        """The complement of the trunk predicate reads and never writes."""
        step = get_step(RESTORE_STEP)

        assert step["uses"] == f"actions/cache/restore@{CACHE_REVISION}", step["uses"]
        assert _normalized(step["if"]) == f"${{{{ {OWNS} && !({TRUNK_PUSH}) }}}}", (
            f"the restoring step must run off the trunk only, not {step['if']!r}"
        )

    @pytest.mark.parametrize("field", ["path", "key", "restore-keys"])
    def test_both_arms_read_one_cache(self, field: str) -> None:
        """A pull request restores exactly what the trunk saved."""
        trunk = get_step(TRUNK_STEP)["with"][field]
        restore = get_step(RESTORE_STEP)["with"][field]

        assert trunk == restore, f"{field} differs: {trunk!r} and {restore!r}"


def _run_key(tmp_path: Path, *, discriminator: str, lock_hash: str) -> dict[str, str]:
    """Run the key fragment with a stub `rustc` and return its outputs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    rustc = bin_dir / "rustc"
    rustc.write_text("#!/bin/sh\necho 'rustc 1.89.0 (29483883e 2025-08-04)'\n")
    rustc.chmod(rustc.stat().st_mode | stat.S_IEXEC)
    output = tmp_path / "github-output"
    output.touch()
    script = get_step(KEY_STEP)["run"]
    assert isinstance(script, str), "the key step must be a shell fragment"
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_RUN_ID": "4242",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
        "SR_DISCRIMINATOR": discriminator,
        "SR_LOCK_HASH": lock_hash,
    }
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", script],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    lines = output.read_text(encoding="utf-8").splitlines()
    key = next(line for line in lines if line.startswith("key="))
    start = lines.index("restore-keys<<SETUP_RUST_RESTORE_KEYS")
    end = lines.index("SETUP_RUST_RESTORE_KEYS")
    return {
        "key": key.removeprefix("key="),
        "restore-keys": "\n".join(lines[start + 1 : end]),
    }


class TestKey:
    """What one lane's key is made of."""

    def test_the_key_names_the_lane_then_the_lockfile_then_the_run(
        self, tmp_path: Path
    ) -> None:
        """Every save is a new entry, and fallbacks widen one step at a time.

        The first restore key keeps the lockfile and drops only the run, so a
        rerun takes the trunk's newest entry for the same dependencies. The
        second drops the lockfile, so a dependency bump still starts warm.
        """
        outputs = _run_key(tmp_path, discriminator="build", lock_hash="abc123")
        lane = outputs["restore-keys"].splitlines()[1]

        assert lane.startswith("sccache-Linux-X64-"), lane
        assert lane.endswith("-build-"), lane
        assert outputs["key"] == f"{lane}abc123-4242", outputs
        assert outputs["restore-keys"] == f"{lane}abc123-\n{lane}", outputs

    def test_the_compiler_is_part_of_the_lane(self, tmp_path: Path) -> None:
        """Objects from another compiler would miss anyway; do not fetch them."""
        outputs = _run_key(tmp_path, discriminator="build", lock_hash="abc123")
        compiler = outputs["key"].removeprefix("sccache-Linux-X64-").split("-")[0]

        assert compiler.isdigit(), f"no compiler hash in {outputs['key']!r}"

    def test_the_discriminator_separates_lanes(self, tmp_path: Path) -> None:
        """Two matrix entries on one runner must not share a lane."""
        first = _run_key(tmp_path / "a", discriminator="lint", lock_hash="x")
        second = _run_key(tmp_path / "b", discriminator="test", lock_hash="x")

        assert first["key"] != second["key"], first
        assert first["restore-keys"] != second["restore-keys"], first

    def test_a_free_text_discriminator_cannot_break_the_key(
        self, tmp_path: Path
    ) -> None:
        """A comma is not allowed in a cache key; nor is a line break here."""
        outputs = _run_key(tmp_path, discriminator="a,b c\nd", lock_hash="x")

        assert "-a_b_c_d-" in outputs["key"], outputs["key"]
        assert "," not in outputs["key"], outputs["key"]
