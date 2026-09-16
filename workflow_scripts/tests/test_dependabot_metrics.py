"""What the auto-merge run reports about its own GitHub calls.

The script already says what it decided. It said nothing about how long
a call took or how many attempts it needed, and those are properties of
the population rather than of one run: how often the merge-state refresh
retries, and which call is slow when a job times out.

Every value asserted here is drawn from a closed set or is a count. A
raw duration, a repository name or a pull-request number as a value
would make each run its own series, which is what makes a rate
uncountable.
"""

from __future__ import annotations

import typing as typ

import pytest

from workflow_scripts import dependabot_automerge

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from workflow_scripts.dependabot_github import GraphQLQuery
from workflow_scripts.dependabot_decision import MergeMethod
from workflow_scripts.dependabot_metrics import (
    LATENCY_BUCKETS,
    METRIC_PREFIX,
    SLOWEST_BUCKET,
    Operation,
    Outcome,
    latency_bucket,
    measured,
)
from workflow_scripts.tests.dependabot_graphql_double import (
    ARMED,
    Branch,
    build_graphql,
    commit_node,
)

TEST_TOKEN = "test-token"  # noqa: S105 - a stand-in, not a credential
REPO = "acme/example"
PR_NUMBER = 7


def _run(handler: GraphQLQuery) -> dependabot_automerge.LiveRun:
    """Return a live run over ``handler`` with the ordinary configuration."""
    return dependabot_automerge.LiveRun(
        token=TEST_TOKEN,
        query=handler,
        config=dependabot_automerge.AutomergeConfig(
            merge_method=MergeMethod.SQUASH,
            required_label="dependencies",
            dry_run=False,
        ),
    )


def _context() -> dependabot_automerge.RuntimeContext:
    """Return a runtime context naming a pull request."""
    return dependabot_automerge.RuntimeContext(
        repo_full_name=REPO,
        event=None,
        pull_request_number=PR_NUMBER,
    )


def _metrics(captured: str) -> dict[str, str]:
    """Return the metric lines in ``captured``, by name."""
    found: dict[str, str] = {}
    for line in captured.splitlines():
        if line.startswith(METRIC_PREFIX):
            name, _, value = line.removeprefix(METRIC_PREFIX).partition("=")
            found[name] = value
    return found


def _metric_lines(captured: str) -> list[str]:
    """Return every metric line in ``captured``."""
    return [line for line in captured.splitlines() if line.startswith(METRIC_PREFIX)]


#: A branch Dependabot wrote entirely, ready to have auto-merge armed.
CLEAN = Branch(pages=[[commit_node("aaaaaaaa1111", "dependabot[bot]")]])

#: The same branch with a commit Dependabot did not write, and a request
#: already armed, so the run withdraws it.
FOREIGN_AND_ARMED = Branch(
    pages=[
        [
            commit_node("aaaaaaaa1111", "dependabot[bot]"),
            commit_node("cccccccc3333", "someone-else"),
        ]
    ],
    auto_merge_request=ARMED,
)


class TestTheLatencyLabelsAreBounded:
    """A duration is reported as a bucket, never as itself."""

    def test_the_label_set_is_closed(self) -> None:
        """Every duration lands in one of five labels and no other.

        An unbounded label is the defect this guards: a raw duration as
        a value makes every run its own series.
        """
        labels = {latency_bucket(tick / 8) for tick in range(0, 1000)}
        allowed = {label for _bound, label in LATENCY_BUCKETS} | {SLOWEST_BUCKET}

        assert labels <= allowed, labels

    def test_a_bound_falls_on_the_side_it_is_named_for(self) -> None:
        """The bound is exclusive, so a bucket never includes its name."""
        assert latency_bucket(0.499) == "under-500ms"
        assert latency_bucket(0.5) == "under-2s"
        assert latency_bucket(59.9) == "under-60s"
        assert latency_bucket(60.0) == SLOWEST_BUCKET


class TestEveryOperationIsMeasured:
    """Each GitHub call reports an outcome, an attempt count and a bucket."""

    def test_arming_reports_the_lookup_the_refresh_and_the_enable(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The ordinary run measures all three of its calls.

        The successes are counted too, or a rate has no denominator.
        """
        handler, _calls = build_graphql(CLEAN)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        found = _metrics(capsys.readouterr().out)
        for operation in (Operation.LOOKUP, Operation.REFRESH, Operation.ENABLE):
            assert found[f"{operation}.outcome"] == Outcome.OK, (operation, found)
            assert f"{operation}.latency" in found, (operation, found)
        assert f"{Operation.MERGE}.outcome" not in found, (
            "arming a request is not merging one, and the two must stay "
            f"distinguishable in the log: {found}"
        )

    def test_a_withdrawal_reports_itself(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Withdrawing an armed request is a call, so it is counted.

        This is the one mutation the script makes on a branch it is
        refusing, and it was previously visible only as prose.
        """
        handler, _calls = build_graphql(FOREIGN_AND_ARMED)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        found = _metrics(capsys.readouterr().out)
        assert found[f"{Operation.DISABLE}.outcome"] == Outcome.OK, found
        assert found[f"{Operation.DISABLE}.attempts"] == "1", found

    def test_a_failed_call_is_counted_rather_than_lost(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A call that raises still reports, because `finally` does.

        A retry rate read only from the runs that worked says nothing
        about the failures the retry did not prevent, which are the ones
        worth knowing about.
        """

        def _fails() -> None:
            """Fail inside the measurement, as a GitHub call would."""
            with measured(Operation.LOOKUP):
                message = "the call did not complete"
                raise RuntimeError(message)

        with pytest.raises(RuntimeError, match="did not complete"):
            _fails()

        found = _metrics(capsys.readouterr().out)
        assert found[f"{Operation.LOOKUP}.outcome"] == Outcome.FAILED, found


class TestNoMetricLineCarriesASecretOrAnUnboundedLabel:
    """A metric is a label, not a log line.

    A token in a workflow log is a leak, and a repository name or a pull
    request number as a value is an unbounded series. Both are easy to
    add by accident and neither is visible in the value a test happens
    to assert.
    """

    def test_an_arming_run_leaks_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The whole happy path is checked, line by line."""
        handler, _calls = build_graphql(CLEAN)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        lines = _metric_lines(capsys.readouterr().out)
        assert lines, "the run reported no metric at all"
        for line in lines:
            assert TEST_TOKEN not in line, line
            assert REPO not in line, line
            assert "acme" not in line, line
            assert str(PR_NUMBER) not in line.split("=", 1)[0], line

    def test_a_withdrawing_run_leaks_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The refusing path carries a commit author, so it is checked too."""
        handler, _calls = build_graphql(FOREIGN_AND_ARMED)

        dependabot_automerge._handle_live_execution(_context(), run=_run(handler))

        lines = _metric_lines(capsys.readouterr().out)
        assert lines, "the run reported no metric at all"
        for line in lines:
            assert TEST_TOKEN not in line, line
            assert "someone-else" not in line, line
            assert "dependabot" not in line.split("=", 1)[1], line
