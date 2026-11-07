"""Cases for a publisher whose trunk coverage is split across jobs.

The measuring job hands its figures to a baseline job and a build-only job
warms the compiler cache. The rules about what a generator measures and
ratchets must hold the measuring job and ignore the build-only one, and the
split inputs must not count as a difference between a lane and the publisher.
"""

from __future__ import annotations

from contract_fixtures import PIN, PUBLISHER, SHARED, TREE, tree, violations

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


def test_the_hand_over_inputs_do_not_differ_a_lane_from_the_publisher() -> None:
    """`publish-baseline: never` and the artefact name select nothing measured."""
    publisher = PUBLISHER.replace(
        "          with-ratchet: 'true'\n",
        "          with-ratchet: 'true'\n"
        "          publish-baseline: never\n"
        "          baseline-artefact-name: coverage-baseline-handover\n",
    )
    assert publisher != PUBLISHER

    assert violations(tree(coverage_main=publisher)) == violations(TREE)
