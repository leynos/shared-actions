"""A scripted GitHub, for driving the auto-merge helper end to end.

The commit audit's tests need a GitHub that can be told what to hold: a
branch of a given shape, spread over pages, with or without an armed
auto-merge request, and answering differently on the second fetch so a
push inside the merge-state retry window can be described. That fixture
lives here rather than beside any one suite, because the behavioural
tests and the rule's property tests want different halves of it.

Nothing here reaches the network. :func:`install_graphql` replaces the
helper's GraphQL client with a function over the values below.
"""

from __future__ import annotations

import typing as typ

from workflow_scripts import dependabot_automerge

if typ.TYPE_CHECKING:
    import collections.abc as cabc

if typ.TYPE_CHECKING:
    import pytest

TEST_TOKEN = "test-token"  # noqa: S105

DEPENDABOT = "dependabot[bot]"

#: The head the audit reads, named to every merge mutation.
HEAD_OID = "0" * 40
MAINTAINER = "leynos"


class Mutation(typ.NamedTuple):
    """One mutation the script sent.

    The document is kept alongside the variables because a variable is
    only sent if the document declares it. An assertion on the variables
    alone passes with the field deleted from the input, which is the
    deletion that matters.

    Attributes
    ----------
    document : str
        The GraphQL document.
    variables : dict[str, object]
        The variables sent with it.
    """

    document: str
    variables: dict[str, object]

    def binds(self, name: str) -> bool:
        """Return whether the document passes ``name`` into its input.

        Parameters
        ----------
        name : str
            The input field, such as ``expectedHeadOid``.

        Returns
        -------
        bool
            True when the input names the field and takes it from the
            matching variable.
        """
        return f"{name}: ${name}" in self.document


class GraphQLCalls(typ.NamedTuple):
    """What the script asked GitHub to do.

    Attributes
    ----------
    enable : list[Mutation]
        Each ``enablePullRequestAutoMerge`` call.
    disable : list[Mutation]
        Each ``disablePullRequestAutoMerge`` call.
    merge : list[Mutation]
        Each ``mergePullRequest`` call.
    cursors : list[object]
        The commit cursor of each query, first page included, so a test
        can assert the connection was followed rather than read once.
    """

    enable: list[Mutation]
    disable: list[Mutation]
    merge: list[Mutation]
    cursors: list[object]


def commit_node(oid: str, *logins: str, total: int | None = None) -> dict[str, object]:
    """Build one commit node as the GraphQL query returns it.

    Parameters
    ----------
    oid : str
        The commit SHA.
    *logins : str
        The logins credited on the commit.
    total : int or None
        ``totalCount`` for the author connection. Above the number of
        logins it marks the credit list as truncated; None means it
        matches.

    Returns
    -------
    dict
        The commit node.
    """
    return {
        "commit": {
            "oid": oid,
            "authors": {
                "totalCount": len(logins) if total is None else total,
                "nodes": [{"user": {"login": login}} for login in logins],
            },
        }
    }


def pull_request_node(
    branch: Branch,
    served: cabc.Sequence[cabc.Sequence[dict[str, object]]],
    page_index: int,
) -> dict[str, object]:
    """Build the pull request node for one page of commits.

    The scalar fields come from the branch and the commits from the page
    set in use, which is not always the branch's own: the merge-state
    retry serves a second set, so that a push landing inside the retry
    window can be described.

    Parameters
    ----------
    branch : Branch
        The pull request GitHub should appear to hold.
    served : cabc.Sequence[cabc.Sequence[dict[str, object]]]
        The commit pages this fetch is serving.
    page_index : int
        Which of those pages this response carries.

    Returns
    -------
    dict
        The pull request node.
    """
    nodes = list(served[page_index])
    has_next = page_index + 1 < len(served)
    return {
        "id": "PR_node",
        "number": 7,
        "headRefOid": HEAD_OID,
        "isDraft": branch.is_draft,
        "mergeStateStatus": branch.merge_state,
        "mergeable": "MERGEABLE",
        "author": {"login": DEPENDABOT},
        "labels": {"nodes": [{"name": "dependencies"}]},
        "autoMergeRequest": branch.auto_merge_request,
        "commits": {
            "totalCount": sum(len(page) for page in served),
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": f"cursor-{page_index + 1}" if has_next else None,
            },
            "nodes": nodes,
        },
    }


def mutation_response(
    query: str, variables: dict[str, object], calls: GraphQLCalls
) -> dict[str, object] | None:
    """Record and answer a mutation, or return None for a query.

    Parameters
    ----------
    query : str
        The GraphQL document.
    variables : dict[str, object]
        Its variables, recorded so a test can assert on them.
    calls : GraphQLCalls
        The record to append to.

    Returns
    -------
    dict[str, object] or None
        The mutation's response, or None when this is not a mutation.
    """
    mutations = (
        ("enablePullRequestAutoMerge", calls.enable, {"pullRequest": {"number": 7}}),
        ("disablePullRequestAutoMerge", calls.disable, {"pullRequest": {"number": 7}}),
        (
            "mergePullRequest",
            calls.merge,
            {"pullRequest": {"number": 7, "merged": True}},
        ),
    )
    for name, record, payload in mutations:
        if name in query:
            record.append(Mutation(document=query, variables=variables))
            return {name: payload}
    return None


class _ServedBranch:
    """Serves commit pages, switching sets after the first whole fetch.

    The merge-state retry refetches the pull request from the first page,
    which is the only way a test can put a push inside the retry window:
    the second fetch must see a different branch from the first.
    """

    def __init__(
        self,
        pages: cabc.Sequence[cabc.Sequence[dict[str, object]]],
        later_pages: cabc.Sequence[cabc.Sequence[dict[str, object]]] | None,
    ) -> None:
        self._pages = pages
        self._later_pages = later_pages
        self._fetches = 0

    def page_for(
        self, cursor: object
    ) -> tuple[cabc.Sequence[cabc.Sequence[dict[str, object]]], int]:
        """Return the page set to serve and the index within it.

        Parameters
        ----------
        cursor : object
            The requested commit cursor, None for the first page.

        Returns
        -------
        tuple
            The page set and the index of the requested page.
        """
        if cursor is None:
            self._fetches += 1
            return self._served, 0
        return self._served, int(str(cursor).rsplit("-", 1)[1])

    @property
    def _served(self) -> cabc.Sequence[cabc.Sequence[dict[str, object]]]:
        """Return the page set the current fetch should see.

        Returns
        -------
        cabc.Sequence[cabc.Sequence[dict[str, object]]]
            The commit pages.
        """
        if self._later_pages is not None and self._fetches > 1:
            return self._later_pages
        return self._pages


class Branch(typ.NamedTuple):
    """The pull request a test wants GitHub to appear to hold.

    One value rather than a parameter list, because these four describe
    one situation, and a call naming three of them by keyword read as
    though the fourth had been forgotten.

    Attributes
    ----------
    pages : cabc.Sequence[cabc.Sequence[dict[str, object]]]
        Commit pages served to the first fetch of the pull request.
    auto_merge_request : dict or None
        The ``autoMergeRequest`` field. Not None means already armed.
    merge_state : str
        The ``mergeStateStatus`` to report.
    later_pages : cabc.Sequence[cabc.Sequence[dict[str, object]]] or None
        Commit pages served from the second fetch onward, for the case of
        a push landing inside the merge-state retry window.
    is_draft : bool
        Whether the pull request is a draft, which is a skip reason other
        than a foreign commit.
    """

    pages: cabc.Sequence[cabc.Sequence[dict[str, object]]]
    auto_merge_request: dict[str, object] | None = None
    merge_state: str = "BLOCKED"
    later_pages: cabc.Sequence[cabc.Sequence[dict[str, object]]] | None = None
    is_draft: bool = False


#: An auto-merge request already armed when the run starts.
ARMED: typ.Final[dict[str, object]] = {
    "enabledAt": "2026-09-06T00:00:00Z",
    "mergeMethod": "SQUASH",
}


def install_graphql(monkeypatch: pytest.MonkeyPatch, branch: Branch) -> GraphQLCalls:
    """Replace the GraphQL client with a scripted one, and record calls.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture.
    branch : Branch
        The pull request GitHub should appear to hold.

    Returns
    -------
    GraphQLCalls
        The record the test asserts against.
    """
    calls = GraphQLCalls(enable=[], disable=[], merge=[], cursors=[])
    state = _ServedBranch(branch.pages, branch.later_pages)

    def handler(
        _token: str, query: str, variables: dict[str, object]
    ) -> dict[str, object]:
        mutation = mutation_response(query, variables, calls)
        if mutation is not None:
            return mutation
        cursor = variables.get("commitCursor")
        calls.cursors.append(cursor)
        served, index = state.page_for(cursor)
        return {"repository": {"pullRequest": pull_request_node(branch, served, index)}}

    monkeypatch.setattr(dependabot_automerge, "request_graphql", handler)
    return calls


def run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke the CLI entry point against the mocked API.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        The patching fixture, used to clear the event path.
    """
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    dependabot_automerge.main(
        github_token=TEST_TOKEN,
        options=dependabot_automerge.AutomergeOptions(
            repository="acme/example",
            pull_request_number=7,
        ),
    )
