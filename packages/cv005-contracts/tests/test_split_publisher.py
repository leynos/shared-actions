"""Cases for a publisher whose trunk coverage is split across jobs.

The measuring job hands its figures to a baseline job and a build-only job
warms the compiler cache. The rules about what a generator measures and
ratchets must hold the measuring job and ignore the build-only one, and the
split inputs must not count as a difference between a lane and the publisher.
"""

from __future__ import annotations

from contract_fixtures import (
    PIN,
    PUBLISHER,
    PULL_REQUEST_LANE,
    SHARED,
    TREE,
    tree,
    violations,
)
from cv005_contracts.lanes import pull_request_lane_violations
from cv005_contracts.loading import load_workflow

WARMUP = (
    "  coverage-warmup:\n"
    "    runs-on: ubuntu-latest\n"
    "    permissions:\n"
    "      contents: read\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          persist-credentials: false\n"
    "      - name: Compile the instrumented tests\n"
    f"        uses: {SHARED}/generate-coverage@{PIN}\n"
    "        with:\n"
    "          output-path: coverage.xml\n"
    "          build-only: 'true'\n"
)


def _publisher_with(job: str) -> str:
    """Return the fixture publisher with *job* appended under `jobs:`."""
    return PUBLISHER.rstrip("\n") + "\n" + job


def test_a_build_only_job_is_not_a_second_generator() -> None:
    """A step that measures nothing owes no ratchet and no selection."""
    texts = tree(coverage_main=_publisher_with(WARMUP))

    assert violations(texts) == violations(TREE), violations(texts)


def test_the_same_job_without_build_only_is_refused() -> None:
    """Dropping `build-only` makes it a generator that does not ratchet."""
    texts = tree(
        coverage_main=_publisher_with(
            WARMUP.replace("          build-only: 'true'\n", "")
        )
    )

    found = violations(texts)

    assert any("must set with-ratchet: 'true' on exactly one" in f for f in found), (
        found
    )


HAND_OVER = (
    "          with-ratchet: 'true'\n"
    "          publish-baseline: never\n"
    "          baseline-artefact-name: coverage-baseline-handover\n"
)
BASELINE_JOB = (
    "  coverage-baseline:\n"
    "    needs: coverage-upload\n"
    "    runs-on: ubuntu-latest\n"
    "    permissions:\n"
    "      contents: read\n"
    "    steps:\n"
    f"      - uses: {SHARED}/publish-coverage-baseline@{PIN}\n"
    "        with:\n"
    "          artefact-name: coverage-baseline-handover\n"
)


def _handing_over(job: str = BASELINE_JOB) -> str:
    """Return the fixture publisher handing over, with *job* receiving it."""
    publisher = PUBLISHER.replace("          with-ratchet: 'true'\n", HAND_OVER)
    assert publisher != PUBLISHER
    return publisher.rstrip("\n") + "\n" + job


def test_the_hand_over_inputs_do_not_differ_a_lane_from_the_publisher() -> None:
    """`publish-baseline: never` and the artefact name select nothing measured."""
    assert violations(tree(coverage_main=_handing_over())) == violations(TREE)


def test_a_hand_over_with_no_baseline_publisher_is_refused() -> None:
    """`never` without a receiving job would freeze the baseline silently."""
    found = violations(tree(coverage_main=_handing_over(job="")))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_baseline_publisher_on_another_artefact_is_refused() -> None:
    """The receiving job must publish the artefact the generator names."""
    other = BASELINE_JOB.replace("coverage-baseline-handover", "something-else")

    found = violations(tree(coverage_main=_handing_over(job=other)))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_baseline_publisher_that_does_not_need_the_generator_is_refused() -> None:
    """Without `needs` it could run, or fail, whatever the measuring job did."""
    unordered = BASELINE_JOB.replace("    needs: coverage-upload\n", "")

    found = violations(tree(coverage_main=_handing_over(job=unordered)))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_lane_may_not_set_build_only() -> None:
    """`build-only` exempts a step from the generator rules; a lane has none."""
    lane = PULL_REQUEST_LANE + "          build-only: 'true'\n"

    found = pull_request_lane_violations({"ci.yml": load_workflow(lane)})

    assert "ci.yml: generate-coverage may not set build-only" in found, found


def test_a_build_only_step_must_share_the_commit_pin() -> None:
    """An older revision would not know the input, and would measure."""
    other = WARMUP.replace(PIN, "b" * 40)

    found = violations(tree(coverage_main=_publisher_with(other)))

    assert any("must share one commit pin" in f for f in found), found


def test_a_skipped_baseline_publisher_is_refused() -> None:
    """A step that can be skipped leaves the baseline unchanged under green."""
    skipped = BASELINE_JOB.replace(
        f"      - uses: {SHARED}/publish-coverage-baseline@{PIN}\n",
        "      - if: ${{ false }}\n"
        f"        uses: {SHARED}/publish-coverage-baseline@{PIN}\n",
    )
    assert skipped != BASELINE_JOB

    found = violations(tree(coverage_main=_handing_over(job=skipped)))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_baseline_publisher_that_excuses_its_failure_is_refused() -> None:
    """`continue-on-error` would turn a tripped ratchet into a green run."""
    excused = BASELINE_JOB.replace(
        "        with:\n          artefact-name",
        "        continue-on-error: true\n        with:\n          artefact-name",
    )
    assert excused != BASELINE_JOB

    found = violations(tree(coverage_main=_handing_over(job=excused)))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_baseline_publisher_on_other_paths_is_refused() -> None:
    """The paths are part of the cache version, so lanes would never restore."""
    moved = BASELINE_JOB + "          baseline-rust-file: elsewhere/.rust\n"

    found = violations(tree(coverage_main=_handing_over(job=moved)))

    assert any("runs publish-coverage-baseline" in f for f in found), found


def test_a_baseline_publisher_on_the_generators_paths_is_accepted() -> None:
    """Custom paths are fine when both sides name the same ones."""
    publisher = PUBLISHER.replace(
        "          with-ratchet: 'true'\n",
        HAND_OVER + "          baseline-rust-file: elsewhere/.rust\n",
    )
    moved = BASELINE_JOB + "          baseline-rust-file: elsewhere/.rust\n"
    texts = tree(coverage_main=publisher.rstrip("\n") + "\n" + moved)

    assert not any("runs publish-coverage-baseline" in f for f in violations(texts))


def test_explicit_default_paths_match_omitted_ones() -> None:
    """Both spell the same path, so the publisher may state what the generator omits."""
    explicit = BASELINE_JOB + "          baseline-rust-file: .coverage-baseline.rust\n"

    texts = tree(coverage_main=_handing_over(job=explicit))

    assert not any("runs publish-coverage-baseline" in f for f in violations(texts))


def test_a_numeric_build_only_is_still_a_generator() -> None:
    """YAML's 1 equals True in Python but reaches the action as "1"."""
    numeric = WARMUP.replace("build-only: 'true'", "build-only: 1")
    assert numeric != WARMUP

    found = violations(tree(coverage_main=_publisher_with(numeric)))

    assert any("must set with-ratchet: 'true' on exactly one" in f for f in found), (
        found
    )
