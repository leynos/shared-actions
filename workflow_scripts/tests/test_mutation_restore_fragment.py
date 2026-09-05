"""Behavioural tests for the mutants job's restore fragment.

The shape tests next door pin where the step sits and how it is guarded. They
cannot say whether it works: a step named correctly, positioned correctly and
guarded correctly can still fail to move a directory, and the failure would
appear only in a scheduled mutation run on somebody else's repository.

These execute the shipped Bash. The fragment is read out of
``mutation-cargo.yml`` and run under a real shell in temporary directories, so
what is tested is the text that ships rather than a transcription of it. The
fragment is run from a file, because that is how a runner runs a ``run:``
block and the two are not the same invocation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "mutation-cargo.yml"
)
RESTORE_STEP = "Restore workflow source"

pytestmark = pytest.mark.skipif(
    not WORKFLOW_PATH.exists(),
    reason="workflow file not present in this working copy (e.g. inside "
    "mutmut's mutants/ sandbox, which does not copy .github/)",
)


def _restore_fragment() -> str:
    """Return the Bash the restore step declares."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == RESTORE_STEP:
                run = step.get("run")
                assert isinstance(run, str), "the restore step declares no Bash"
                return run
    message = f"no {RESTORE_STEP!r} step in {WORKFLOW_PATH}"
    raise AssertionError(message)


def _run_fragment(
    tmp_path: Path, *, original: str, relocated: str
) -> subprocess.CompletedProcess[str]:
    """Execute the restore fragment with the two directories it reads."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    script = tmp_path / "restore.sh"
    script.write_text(_restore_fragment(), encoding="utf-8")
    return subprocess.run(  # noqa: S603,TID251 - exercise the shipped fragment.
        [bash, str(script)],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
        env={
            "PATH": "/usr/bin:/bin",
            "ORIGINAL_DIR": original,
            "RELOCATED_DIR": relocated,
        },
    )


def _metric(process: subprocess.CompletedProcess[str]) -> str:
    """Return the single outcome metric the fragment emitted."""
    metrics = [
        line.split("=", 1)[1]
        for line in process.stdout.splitlines()
        if line.startswith("metric mutation-restore.result=")
    ]
    assert len(metrics) == 1, (
        f"expected one outcome metric, got {metrics}; stdout was "
        f"{process.stdout!r} and stderr {process.stderr!r}"
    )
    return metrics[0]


@pytest.fixture
def relocated_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """Return a workspace path and a relocated checkout holding one marker."""
    original = tmp_path / "workspace" / "workflow-src"
    relocated = tmp_path / "runner-temp" / "workflow-src"
    relocated.mkdir(parents=True)
    (relocated / ".github").mkdir()
    (relocated / "marker.txt").write_text("moved", encoding="utf-8")
    original.parent.mkdir(parents=True, exist_ok=True)
    return original, relocated


class TestRestoreFragment:
    """Every ending the fragment is written for, executed."""

    def test_it_moves_the_checkout_back(
        self, tmp_path: Path, relocated_checkout: tuple[Path, Path]
    ) -> None:
        """The ordinary case: the tree returns to the workspace."""
        original, relocated = relocated_checkout

        process = _run_fragment(
            tmp_path, original=str(original), relocated=str(relocated)
        )

        assert process.returncode == 0, process.stderr
        assert (original / "marker.txt").read_text(encoding="utf-8") == "moved", (
            "the checkout did not arrive at the workspace path"
        )
        assert not relocated.exists(), "the relocated copy was left behind"
        assert _metric(process) == "restored"

    def test_it_replaces_whatever_occupies_the_workspace_path(
        self, tmp_path: Path, relocated_checkout: tuple[Path, Path]
    ) -> None:
        """A leftover directory must not make `mv` nest the checkout inside it.

        Without the removal, `mv` would put the tree at
        ``workflow-src/workflow-src`` and the post step would still fail, with
        the restore reporting success.
        """
        original, relocated = relocated_checkout
        original.mkdir(parents=True)
        (original / "stale.txt").write_text("stale", encoding="utf-8")

        process = _run_fragment(
            tmp_path, original=str(original), relocated=str(relocated)
        )

        assert process.returncode == 0, process.stderr
        assert (original / "marker.txt").is_file(), "the checkout did not land"
        assert not (original / "stale.txt").exists(), "the stale tree survived"
        assert not (original / "workflow-src").exists(), "the checkout nested"
        assert _metric(process) == "restored"

    def test_it_declines_when_the_relocated_tree_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Nothing to move is not a failure; cleanup must not fail the job."""
        original = tmp_path / "workspace" / "workflow-src"
        original.parent.mkdir(parents=True)

        process = _run_fragment(
            tmp_path,
            original=str(original),
            relocated=str(tmp_path / "runner-temp" / "workflow-src"),
        )

        assert process.returncode == 0, process.stderr
        assert _metric(process) == "absent"
        assert "::warning" in process.stdout, "an absent tree must be annotated"

    def test_it_declines_when_the_paths_are_identical(self, tmp_path: Path) -> None:
        """Under act the workspace is the workflow source, so nothing moved.

        Moving a directory onto itself would destroy it, since the fragment
        removes the destination first.
        """
        shared = tmp_path / "workflow-src"
        shared.mkdir()
        (shared / "marker.txt").write_text("in place", encoding="utf-8")

        process = _run_fragment(tmp_path, original=str(shared), relocated=str(shared))

        assert process.returncode == 0, process.stderr
        assert (shared / "marker.txt").is_file(), "the checkout was destroyed"
        assert _metric(process) == "not-relocated"

    @pytest.mark.parametrize(
        ("original", "relocated"),
        [
            pytest.param("", "/tmp/relocated", id="original-unset"),  # noqa: S108
            pytest.param("/tmp/original", "", id="relocated-unset"),  # noqa: S108
            pytest.param("", "", id="both-unset"),
        ],
    )
    def test_it_declines_when_a_directory_is_unset(
        self, tmp_path: Path, original: str, relocated: str
    ) -> None:
        """An unset path must never reach `rm -rf`."""
        process = _run_fragment(tmp_path, original=original, relocated=relocated)

        assert process.returncode == 0, process.stderr
        assert _metric(process) == "unset-directory"
