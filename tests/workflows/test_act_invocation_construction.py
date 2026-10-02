"""Contract for the act command line the harness builds.

Two of act's flags decide whether a case tests anything at all, and both
fail quietly when they are wrong. `-P` takes one image per runner label,
and act prints `Skipping unsupported platform` for a label it has none for
-- running no step and exiting zero, so every downstream log assertion
finds nothing and the case fails far from its cause. A bind-mounted
checkout carries a `.git` file that act cannot follow out of the mount,
and a step that reads git fails with `fatal: not a git repository:
(null)`. This module holds the arguments that answer both, and the guard
that refuses a case act could not execute.

Nothing here runs act or needs a container runtime; the command line is
built and read back.
"""

from __future__ import annotations

import typing as typ

import pytest
from plumbum import CommandNotFound, local

from . import _workflow_reading as reading
from . import conftest

if typ.TYPE_CHECKING:
    from pathlib import Path

#: A case whose workflow and job the guard accepts, used to build command
#: lines. `ubuntu-latest` is named outright, so the expected arguments do
#: not depend on the expression reader.
_WORKFLOW: typ.Final[str] = "test-install-whitaker.yml"
_JOB: typ.Final[str] = "install-whitaker"


def _invocation(tmp_path: Path) -> conftest.ActInvocation:
    """Return an invocation with every field the command line reads."""
    event_path = tmp_path / "event.json"
    event_path.write_text("{}", encoding="utf-8")
    return conftest.ActInvocation(
        workflow=_WORKFLOW,
        event="workflow_dispatch",
        job=_JOB,
        event_path=event_path,
        artefact_dir=tmp_path / "artefacts",
        container_env={},
    )


def _flag_values(args: list[str], flag: str) -> list[str]:
    """Return every value given to *flag*, in order."""
    return [
        value for index, value in enumerate(args) if index and args[index - 1] == flag
    ]


def _git(*args: str) -> None:
    """Run one git command in the temporary repository under construction.

    Raises
    ------
    pytest.skip.Exception
        If git cannot run here at all, which is not a fact about the harness
        under test.
    """
    try:
        completed = local["git"][list(args)].run(retcode=None)
    except (CommandNotFound, OSError) as exc:
        pytest.skip(f"git cannot run here: {exc}")
    if completed[0] != 0:
        pytest.skip(f"git {args} failed: {completed[2].strip()}")


def _linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Return a linked worktree and the store its `.git` file points at.

    `git worktree add` will not attach to a repository with no commit, so
    the store is given one first. The worktree is then the shape the
    harness's mount exists for: a `.git` file naming a directory outside
    the checkout.
    """
    store = tmp_path / "bare.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"

    _git("init", str(seed))
    _git("-C", str(seed), "config", "user.email", "harness@example.invalid")
    _git("-C", str(seed), "config", "user.name", "Harness")
    _git("-C", str(seed), "commit", "--allow-empty", "-m", "seed")
    # Cloned bare rather than initialized bare: a fresh bare repository has
    # no commit for `worktree add` to check out and refuses to attach one.
    _git("clone", "--bare", str(seed), str(store))
    _git("-C", str(store), "worktree", "add", "--detach", str(checkout))

    if not (checkout / ".git").is_file():
        msg = "git did not make a linked worktree for this case"
        raise AssertionError(msg)
    return checkout, store


class TestPlatformImages:
    """Every Linux label the fixtures may name has an image."""

    def test_the_image_is_given_for_every_label_in_the_vocabulary(self) -> None:
        """`-P` covers the label vocabulary, not one label of it.

        The fixtures name `ubuntu-latest` and `ubicloud-standard-2` alike,
        and act skips a job whose label it has no image for. Reading the
        labels from the shared vocabulary rather than writing them out is
        what lets this assertion be the whole rule: a label added to the
        vocabulary and not to the map fails here.
        """
        images = conftest._platform_images()
        labels = {
            reading.UBICLOUD_LINUX,
            reading.HOSTED_LINUX,
        }

        assert labels == reading.RECOGNIZED_LINUX_LABELS
        assert [image.split("=", 1)[0] for image in images] == sorted(labels), (
            "every label in the vocabulary must be given an image, and no "
            f"other label may be: got {images}"
        )

    def test_every_image_is_the_same_ubuntu_shape(self) -> None:
        """The image is act's Ubuntu shape, named once.

        The suite exercises `install-whitaker`, so the image has to carry a
        Rust toolchain: the action verifies the extracted `cargo-dylint` with
        `cargo dylint --version` before installing it. This is the clause that
        keeps the image from drifting back to one the lane cannot pass on.
        """
        images = conftest._platform_images()
        assert images, "the map must not be empty: act would skip every job"
        assert {image.split("=", 1)[1] for image in images} == {conftest._ACT_IMAGE}, (
            f"one image per label, all of them {conftest._ACT_IMAGE}: got {images}"
        )
        assert conftest._ACT_IMAGE.endswith(":rust-latest"), (
            f"{conftest._ACT_IMAGE} carries no Rust toolchain, so the "
            "install-whitaker cases would fail the dylint verification probe"
        )

    def test_the_platform_flag_is_repeated_per_label(self, tmp_path: Path) -> None:
        """`-P` takes one value, so it is passed once per label."""
        args = conftest._build_act_args(_invocation(tmp_path))

        assert _flag_values(args, "-P") == conftest._platform_images(), (
            "every -P value must be one label's image, in the map's order"
        )
        # A comma-joined value would be read by act as one platform whose
        # image is a list of them, which is a platform act cannot pull.
        assert not any("," in value for value in _flag_values(args, "-P"))


class TestGitRepositoryMount:
    """The object store reaches the container when it lives outside it."""

    def test_a_linked_worktree_is_given_its_object_store(self, tmp_path: Path) -> None:
        """The option names the store on both sides and mounts it read-only.

        A linked worktree's `.git` is a file pointing outside the checkout,
        which act's bind mount cannot follow; the container is given the
        object store at the same absolute path so the pointer resolves. The
        mode matters: the checkout is mountable read-write and the store is
        shared with every other worktree, so a container that wrote to it
        would rewrite the host's refs mid-run.
        """
        checkout, store = _linked_worktree(tmp_path)
        resolved = str(store.resolve())

        assert conftest._git_common_dir(checkout) == store.resolve()
        assert conftest._git_common_dir_mount(checkout) == (
            f'-v "{resolved}":"{resolved}":ro'
        )

    def test_a_directory_with_no_checkout_yields_no_mount(self, tmp_path: Path) -> None:
        """Nothing to mount is no option, not an option naming nothing.

        A directory that is not a checkout is the case where the probe's
        answer must be None: the command line is built for every case,
        including the ones that never read git, and naming a store that
        does not exist would fail the act run outright.
        """
        assert conftest._git_common_dir(tmp_path) is None
        assert conftest._git_common_dir_mount(tmp_path) is None

    def test_an_ordinary_checkout_is_not_given_one(self, tmp_path: Path) -> None:
        """A `.git` directory inside the checkout needs no mount.

        Mounting the store over the checkout's own `.git` directory would
        shadow the working repository with the bare one, which is at best
        noise and at worst a different repository's history. The checkout is
        built here rather than read from this repository's own root, because
        the host this suite is developed on lives in a linked worktree while
        a GitHub runner checks out ordinarily, and the assertion has to hold
        where it runs: on the runner, which is the case being pinned.
        """
        checkout = tmp_path / "ordinary"
        _git("init", str(checkout))

        common_dir = conftest._git_common_dir(checkout)

        assert common_dir is not None, "git did not answer for its own checkout"
        assert common_dir.is_relative_to(checkout), (
            "an ordinary checkout's object store lives inside it: "
            f"got {common_dir} for {checkout}"
        )
        assert conftest._git_common_dir_mount(checkout) is None, (
            "an ordinary checkout must not be handed a mount that would "
            "shadow its own .git directory"
        )

    def test_the_option_is_carried_by_the_command_line_once(
        self, tmp_path: Path
    ) -> None:
        """The command line carries exactly the option the probe returns."""
        args = conftest._build_act_args(_invocation(tmp_path))
        mount = conftest._git_common_dir_mount(conftest._REPOSITORY_ROOT)

        assert _flag_values(args, "--container-options") == (
            [mount] if mount is not None else []
        ), "act is given one container option, and only when there is a mount"


class TestImageRequirement:
    """The guard refuses a case act would skip in silence."""

    def test_a_linux_job_is_accepted(self) -> None:
        """A job naming a mapped label passes the guard."""
        conftest._require_an_image_for(_WORKFLOW, _JOB)

    def test_a_label_act_has_no_image_for_is_refused(self) -> None:
        """A Windows job is refused, because act can run no step of it."""
        with pytest.raises(ValueError, match="run on a runner outside"):
            conftest._require_an_image_for(
                "test-install-whitaker.yml", "install-whitaker-windows"
            )

    def test_a_job_that_does_not_exist_is_refused(self) -> None:
        """A case naming a job the workflow does not have is refused.

        act exits zero without running anything for a job it cannot find,
        so a renamed job would leave its cases passing while testing
        nothing.
        """
        with pytest.raises(TypeError, match="is not a job of"):
            conftest._require_an_image_for(_WORKFLOW, "no-such-job")

    def test_a_matrix_job_is_refused(self) -> None:
        """A job whose runner comes from a matrix of labels is refused.

        `ci.yml`'s suite runs on Linux inside a matrix that also offers a
        macOS leg. act would run the Linux leg and skip the other, and the
        harness cannot say which of them a case meant, so it says so.
        """
        with pytest.raises(ValueError, match="run on a runner outside"):
            conftest._require_an_image_for("ci.yml", "python-tests")

    def test_a_callee_jobs_are_judged_for_a_reusable_workflow(self) -> None:
        """A job that only calls a workflow is judged by the callee's jobs.

        The mutation cases drive `test-mutation-cargo.yml`, whose only job
        is a `uses:` with no `runs-on`; act runs the callee's jobs, so
        their labels are the ones that need an image.
        """
        conftest._require_an_image_for("test-mutation-cargo.yml", "mutation")


class TestForkFallbackResolution:
    """The guard reads a `runs-on` expression as the labels it can reach."""

    def test_the_fork_fallback_names_two_mapped_labels(self) -> None:
        """The expression the fixtures use is accepted.

        Every act-driven fixture selects its runner with a `&&`/`||` chain
        of two quoted labels. Both are Linux and both are mapped, so act
        can run the job on either arm.
        """
        runs_on = (
            "${{ github.event.pull_request.head.repo.fork"
            " && 'ubuntu-latest' || 'ubicloud-standard-2' }}"
        )
        assert conftest._labels_named_by(runs_on) == [
            reading.HOSTED_LINUX,
            reading.UBICLOUD_LINUX,
        ]
        assert conftest._resolves_to_one_platform({"runs-on": runs_on})

    def test_a_value_compared_against_is_not_read_as_a_label(self) -> None:
        """Only labels the vocabulary knows are read as runners.

        A context reference such as `github.event_name` compares against
        `'schedule'`, which is quoted like a label and is not one. Reading
        it as a label would have the guard judge a job by a runner it never
        runs on.
        """
        runs_on = "${{ github.event_name == 'workflow_dispatch' && 'ubuntu-latest' }}"
        assert conftest._labels_named_by(runs_on) == [reading.HOSTED_LINUX]
        assert conftest._resolves_to_one_platform({"runs-on": runs_on})

    def test_one_non_linux_arm_refuses_the_whole_expression(self) -> None:
        """A label act has no image for refuses the case, whatever it sits beside.

        act runs the Linux arm of a `&&`/`||` chain and skips the other, so
        a case whose expression can reach a macOS job would sometimes run a
        step and sometimes run nothing. The harness refuses rather than
        having the same case mean two things.
        """
        runs_on = (
            "${{ github.event_name == 'schedule' && 'macos-15' || 'ubuntu-latest' }}"
        )
        assert conftest._labels_named_by(runs_on) == [
            "macos-15",
            reading.HOSTED_LINUX,
        ]
        assert not conftest._resolves_to_one_platform({"runs-on": runs_on})

    def test_a_job_with_no_runs_on_is_refused(self) -> None:
        """A job that names no runner cannot be given an image."""
        assert not conftest._resolves_to_one_platform({})

    @pytest.mark.parametrize(
        "runs_on",
        [
            pytest.param("windows-latest", id="windows"),
            pytest.param("macos-15", id="macos"),
            pytest.param("ubuntu-24.04-arm", id="arm"),
        ],
    )
    def test_a_non_linux_label_is_refused(self, runs_on: str) -> None:
        """A label act has no image for is refused, not silently skipped."""
        assert not conftest._resolves_to_one_platform({"runs-on": runs_on})

    def test_a_matrix_binding_is_read_from_its_legs(self) -> None:
        """A whole-string matrix reference is judged by its legs.

        One leg, all Ubicloud: the expression resolves to a single platform
        and act can run the job. This is the reading that keeps a
        matrix-shaped job from being accepted on the strength of being
        spelled as an expression.
        """
        job = {
            "runs-on": "${{ matrix.os }}",
            "strategy": {"matrix": {"include": [{"os": reading.UBICLOUD_LINUX}]}},
        }
        assert conftest._resolves_to_one_platform(job)

    def test_a_matrix_binding_to_two_platforms_is_refused(self) -> None:
        """Two legs are two platforms, which one `-P` map cannot cover."""
        job = {
            "runs-on": "${{ matrix.os }}",
            "strategy": {
                "matrix": {
                    "include": [
                        {"os": reading.UBICLOUD_LINUX},
                        {"os": reading.HOSTED_LINUX},
                    ]
                }
            },
        }
        assert not conftest._resolves_to_one_platform(job)
