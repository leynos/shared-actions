"""Contracts for the GraphQL call the live auto-merge path is given.

The live orchestration takes one GraphQL call and threads it through
every read and every mutation. Nothing below :func:`main` may resolve a
client of its own, because a fallback to the module global is invisible
while a test patches that global: the run passes, and the argument it
was handed is never used on the path the fallback covers.

These tests therefore poison the module global and supply the call as an
argument. Any function that reaches for ``request_graphql`` instead of
its argument raises, and names itself in the failure.
"""

from __future__ import annotations

import inspect
import typing as typ

import pytest

from workflow_scripts import dependabot_automerge
from workflow_scripts.tests.dependabot_graphql_double import (
    ARMED,
    DEPENDABOT,
    MAINTAINER,
    Branch,
    build_graphql,
    commit_node,
)

if typ.TYPE_CHECKING:
    from workflow_scripts.tests.dependabot_graphql_double import GraphQLCalls

TEST_TOKEN = "test-token"  # noqa: S105


class UnexpectedLiveClientError(RuntimeError):
    """Raised when the live path falls back to the module-global client."""


def _poison_module_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any use of the module-global GraphQL client fail loudly.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture.
    """

    def refuse(
        _token: str, _query: str, _variables: dict[str, object]
    ) -> dict[str, object]:
        message = (
            "the live path reached for the module-global request_graphql "
            "instead of the GraphQL call it was given"
        )
        raise UnexpectedLiveClientError(message)

    monkeypatch.setattr(dependabot_automerge, "request_graphql", refuse)


def _run_live(
    monkeypatch: pytest.MonkeyPatch,
    branch: Branch,
) -> GraphQLCalls:
    """Drive the live path with an injected call and a poisoned global.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture.
    branch : Branch
        The pull request GitHub should appear to hold.

    Returns
    -------
    GraphQLCalls
        What the injected call was asked to do.
    """
    _poison_module_global(monkeypatch)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    handler, calls = build_graphql(branch)
    dependabot_automerge._handle_live_execution(
        dependabot_automerge.RuntimeContext(
            repo_full_name="acme/example",
            event=None,
            pull_request_number=7,
        ),
        run=dependabot_automerge.LiveRun(
            token=TEST_TOKEN,
            query=handler,
            config=dependabot_automerge.AutomergeConfig(
                merge_method="SQUASH",
                required_label="dependencies",
                dry_run=False,
            ),
        ),
    )
    return calls


def _mutation_counts(calls: GraphQLCalls) -> tuple[int, int, int]:
    """Return the enable, disable and merge counts as one value.

    Parameters
    ----------
    calls : GraphQLCalls
        The record to summarize.

    Returns
    -------
    tuple[int, int, int]
        Enable, disable and merge call counts.
    """
    return len(calls.enable), len(calls.disable), len(calls.merge)


DEPENDABOT_ONLY = (commit_node("a" * 40, DEPENDABOT),)
WITH_A_FOREIGN_COMMIT = (
    commit_node("a" * 40, DEPENDABOT),
    commit_node("b" * 40, MAINTAINER),
)


class MutatingPath(typ.NamedTuple):
    """One mutating path the live run has, and what it must produce.

    Attributes
    ----------
    branch : Branch
        The pull request GitHub should appear to hold to reach it.
    counts : tuple[int, int, int]
        The enable, disable and merge counts the path must produce.
    status : str
        The ``automerge_status`` the run must report.
    """

    branch: Branch
    counts: tuple[int, int, int]
    status: str


#: Every mutating path the live run has. Exhaustive rather than
#: sampled: arming, withdrawal and direct merge are the whole set of
#: mutations the run can send.
MUTATING_PATHS = (
    pytest.param(
        MutatingPath(
            branch=Branch(pages=[list(DEPENDABOT_ONLY)]),
            counts=(1, 0, 0),
            status="enabled",
        ),
        id="arming",
    ),
    pytest.param(
        MutatingPath(
            branch=Branch(
                pages=[list(WITH_A_FOREIGN_COMMIT)], auto_merge_request=ARMED
            ),
            counts=(0, 1, 0),
            status="cancelled",
        ),
        id="withdrawal",
    ),
    pytest.param(
        MutatingPath(
            branch=Branch(pages=[list(DEPENDABOT_ONLY)], merge_state="CLEAN"),
            counts=(0, 0, 1),
            status="merged",
        ),
        id="direct-merge",
    ),
)


class TestGraphQLInjection:
    """The boundary holds: nothing below `main` resolves a client itself.

    Grouped because both tests are about the same contract from two
    sides. One drives every mutating path with a poisoned module global
    and an injected call, so a fallback raises. The other reads the
    signature the fallback would have to come back through.
    """

    @pytest.mark.parametrize("path", MUTATING_PATHS)
    def test_every_mutating_path_uses_the_injected_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        path: MutatingPath,
    ) -> None:
        """Each mutating path sends its mutation through the supplied call.

        Arming, withdrawal and direct merge are three separate paths, and a
        fallback on any one of them would be hidden by the module-global
        patch the rest of the suite relies on.

        Parameters
        ----------
        monkeypatch : pytest.MonkeyPatch
            The patching fixture.
        capsys : pytest.CaptureFixture[str]
            Captures the decision the run reports.
        path : MutatingPath
            The path under test, and what it must produce.
        """
        calls = _run_live(monkeypatch, path.branch)

        assert _mutation_counts(calls) == path.counts, (
            "the mutation must reach the injected call, and no other mutation "
            f"with it; expected {path.counts}, got {_mutation_counts(calls)}"
        )
        assert f"automerge_status={path.status}" in capsys.readouterr().out, (
            f"the run must report {path.status}"
        )

    def test_the_boundary_reader_takes_no_default_call(self) -> None:
        """The GitHub reader requires the call; it names no client itself.

        The default is what a fallback would reintroduce, so its absence is
        asserted on the signature rather than inferred from behaviour.
        """
        parameter = inspect.signature(
            dependabot_automerge.fetch_pull_request
        ).parameters["query"]
        assert parameter.default is inspect.Parameter.empty, (
            "fetch_pull_request must require its GraphQL call, so that choosing "
            "the live client stays at the composition root"
        )
