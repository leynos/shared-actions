"""Cases for pairing each lane leg with a publisher leg of the same selection.

The estate's lanes carry tool pins the generator never reads, and some
publishers measure more than one leg. These cases hold both directions:
what cannot change a measurement is ignored, and anything else is still
compared.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PIN, PUBLISHER, PULL_REQUEST_LANE, SHARED, tree
from cv005_contracts.lanes import pull_request_lane_violations
from cv005_contracts.loading import load_workflow
from cv005_contracts.parity import publisher_lane_violations

LANE_JOB = "    runs-on: ubuntu-latest\n    permissions:\n"
CHECK_NAME = "      - name: Check for the CodeScene token\n"
#: The fixture publisher's one coverage step.
GENERATOR = PUBLISHER[
    PUBLISHER.index("      - name: Generate coverage\n") : PUBLISHER.index(CHECK_NAME)
]


def _parity(publisher: str, lane: str) -> list[str]:
    """Return the parity findings for one publisher and one lane text."""
    closure = {"ci.yml": load_workflow(lane)}
    return publisher_lane_violations(load_workflow(publisher), closure)


def _lane_leg(extra: str = "") -> str:
    """Return a second, unratcheted lane leg measuring a smaller selection."""
    return (
        "      - name: Second leg\n"
        "        if: github.event_name == 'pull_request'\n"
        "        env:\n          UV_PYTHON: '3.13'\n"
        f"        uses: {SHARED}/generate-coverage@{PIN}\n"
        "        with:\n          output-path: other.xml\n"
        f"          features: minimal\n          publish-artefact: 'false'\n{extra}"
    )


def _publisher_leg(extra: str = "") -> str:
    """Return the publisher leg the second lane leg pairs with."""
    return (
        "      - name: Second leg\n"
        "        env:\n          UV_PYTHON: '3.13'\n"
        f"        uses: {SHARED}/generate-coverage@{PIN}\n"
        "        with:\n          output-path: other.xml\n"
        f"          features: minimal\n{extra}"
    )


def _with_job_env(text: str, env: str) -> str:
    """Return a lane text whose job carries an extra `env` block."""
    assert text.count(LANE_JOB) == 1, text
    return text.replace(
        LANE_JOB, f"    runs-on: ubuntu-latest\n    env:\n{env}    permissions:\n"
    )


@pytest.mark.parametrize(
    "env",
    [
        "      WHITAKER_INSTALLER_VERSION: '0.2.7'\n",
        "      WHITAKER_REV: abc\n",
        "      ACTIONLINT_SHA256_X64: abc\n",
        "      UV_TOOL_DIR: /tmp/uv\n      CARGO_NET_RETRY: '10'\n",
    ],
)
def test_a_tool_pin_the_generator_never_reads_is_ignored(env: str) -> None:
    """A version, checksum or tool location changes nothing the tests execute."""
    found = _parity(PUBLISHER, _with_job_env(PULL_REQUEST_LANE, env))
    assert not found, found


@pytest.mark.parametrize(
    "env",
    [
        "      RUSTFLAGS: -Cdebug-assertions=off\n",
        "      FEATURE_VERSIONS: all\n",
        "      NETSUKE_RUST_TOOLCHAIN: nightly\n",
    ],
)
def test_an_environment_key_that_may_measure_is_compared(env: str) -> None:
    """Any key but a tool pin reaches cargo, uv or the tests, so it counts."""
    found = _parity(PUBLISHER, _with_job_env(PULL_REQUEST_LANE, env))
    assert found == ["ci.yml: coverage selection differs from the publisher's"], found


def test_publish_baseline_is_the_publishers_alone() -> None:
    """The publisher may choose when to save; that selects nothing measured."""
    publisher = PUBLISHER.replace(
        "          with-ratchet: 'true'\n",
        "          with-ratchet: 'true'\n          publish-baseline: always\n",
    )
    assert publisher != PUBLISHER
    assert not _parity(publisher, PULL_REQUEST_LANE)


def test_a_lane_may_not_set_publish_baseline() -> None:
    """`always` on a lane would save a baseline from a pull request."""
    lane = PULL_REQUEST_LANE + "          publish-baseline: auto\n"
    found = pull_request_lane_violations({"ci.yml": load_workflow(lane)})
    assert found == ["ci.yml: generate-coverage may not set publish-baseline"], found


@pytest.mark.parametrize("ratchet", ["", "          with-ratchet: 'false'\n"])
def test_each_lane_leg_pairs_with_a_publisher_leg(ratchet: str) -> None:
    """ortho-config's shape: two legs each side, one ratcheting per job."""
    publisher = PUBLISHER.replace(CHECK_NAME, _publisher_leg(ratchet) + CHECK_NAME)
    lane = PULL_REQUEST_LANE + _lane_leg()
    lanes = {"ci.yml": load_workflow(lane)}
    found = _parity(publisher, lane) + pull_request_lane_violations(lanes)
    assert not found, found


def test_a_lane_leg_matching_no_publisher_leg_is_refused() -> None:
    """The second leg must match a leg the publisher measures, not any leg."""
    found = _parity(PUBLISHER, PULL_REQUEST_LANE + _lane_leg())
    assert found == ["ci.yml: coverage selection differs from the publisher's"], found


@pytest.mark.parametrize("side", ["publisher", "lane"])
def test_a_job_ratchets_exactly_one_leg(side: str) -> None:
    """The action keys the baseline by job, so a second ratchet overwrites it."""
    ratchet = "          with-ratchet: 'true'\n"
    publisher = PUBLISHER.replace(CHECK_NAME, _publisher_leg(ratchet) + CHECK_NAME)
    lane = PULL_REQUEST_LANE + _lane_leg(ratchet)
    if side == "publisher":
        found = _parity(publisher, PULL_REQUEST_LANE)
    else:
        found = pull_request_lane_violations({"ci.yml": load_workflow(lane)})
    assert any("exactly one" in problem for problem in found), found


def test_a_second_publisher_job_ratchets_its_own_platform() -> None:
    """rstest-bdd's shape: a Windows job writes the Windows baseline."""
    windows = (
        "  coverage-baseline-windows:\n"
        "    runs-on: windows-latest\n"
        "    permissions:\n      contents: read\n"
        "    steps:\n" + GENERATOR
    )
    unrelated = (
        "  notes:\n    runs-on: ubuntu-latest\n    permissions: {}\n"
        "    steps:\n      - run: 'true'\n"
    )
    found = _parity(PUBLISHER + windows + unrelated, tree()["ci.yml"])
    assert not found, found


def test_a_publisher_measuring_nothing_is_refused() -> None:
    """The whitaker shape: no generate-coverage step, so no baseline to pair."""
    found = _parity(PUBLISHER.replace(GENERATOR, ""), PULL_REQUEST_LANE)
    assert found == [
        "the publisher must generate coverage; found no generate-coverage step"
    ], found
