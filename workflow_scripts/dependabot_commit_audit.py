"""Who wrote the commits on a Dependabot branch.

Auto-merge skips human review, so eligibility has to answer more than
"did Dependabot open this?". Opening a pull request is not the same as
writing what is in it, and once Dependabot has opened one, anything
pushed to that branch would otherwise merge under the same rule.

This module owns that reading and nothing else. It is split in two on
purpose:

- the adapter, :func:`commit_page`, is the only place that knows the
  GraphQL shape ``commits.nodes[].commit.authors.nodes[].user.login``;
- the policy, :func:`foreign_commits`, states the rule over
  :class:`CommitRecord` values alone.

Keeping them apart is what lets the rule be exercised without a GitHub
response in the way, and what stops a schema change from quietly meaning
a different rule. Paging the connection and acting on the outcome belong
to :mod:`dependabot_automerge`, which composes these.

See ``docs/developers-guide.md``, "Dependabot auto-merge commit audit".
"""

from __future__ import annotations

import enum
import typing as typ

if typ.TYPE_CHECKING:
    if __package__:
        from .graphql_client import JsonValue
    else:
        from graphql_client import (  # type: ignore[import-not-found,no-redef]
            JsonValue,
        )


class DependabotLogin(enum.StrEnum):
    """Supported Dependabot author login variants.

    Attributes
    ----------
    BOT : DependabotLogin
        The canonical Dependabot bot login (``dependabot[bot]``).
    LEGACY : DependabotLogin
        The legacy Dependabot login (``dependabot``).
    """

    BOT = "dependabot[bot]"
    LEGACY = "dependabot"


DEPENDABOT_LOGINS: frozenset[str] = frozenset(login.value for login in DependabotLogin)

#: Stands in for a commit author GitHub did not name, so a message can
#: still identify the commit.
UNKNOWN_AUTHOR: typ.Final[str] = "an unnamed author"

#: Stands in for the authors of a commit whose credit list came back
#: truncated. Such a commit cannot be certified as Dependabot's, so it is
#: reported as foreign rather than waved through.
UNREAD_CO_AUTHOR: typ.Final[str] = "an unread co-author"

#: How many commits and credited authors one page of the query asks for.
#: Both connections are paged or checked for truncation, because a
#: connection read to its limit and no further is how a foreign commit
#: slips past a check that looks complete.
COMMIT_PAGE_SIZE: typ.Final[int] = 100
AUTHOR_PAGE_SIZE: typ.Final[int] = 100

#: Ceiling on commit pages. Fifty pages is 5,000 commits, far beyond any
#: real dependency branch; stopping there bounds the work rather than
#: trusting a cursor to terminate, and the branch is reported unreadable.
MAX_COMMIT_PAGES: typ.Final[int] = 50


#: The commit connection, shared by the first query and the paging one so
#: the two cannot describe different shapes. ``totalCount`` on ``authors``
#: is what makes a truncated credit list visible.
COMMITS_FRAGMENT = """
      commits(first: $commitPageSize, after: $commitCursor) {
        totalCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          commit {
            oid
            authors(first: $authorPageSize) {
              totalCount
              nodes {
                user {
                  login
                }
              }
            }
          }
        }
      }
"""


class CommitRecord(typ.NamedTuple):
    """One commit on the branch, in terms the eligibility rule uses.

    The GraphQL shape stops here. Everything downstream reads a commit as
    an identifier and the logins credited on it, so the rule can be stated
    and tested without a GitHub response in the way.

    Attributes
    ----------
    oid : str
        The commit SHA.
    authors : tuple[str, ...]
        The logins credited on the commit, with :data:`UNKNOWN_AUTHOR` for
        any GitHub did not name.
    authors_complete : bool
        Whether every credited author was returned. A truncated list
        cannot certify the commit as Dependabot's.
    """

    oid: str
    authors: tuple[str, ...]
    authors_complete: bool = True


class CommitPage(typ.NamedTuple):
    """One page of a branch's commits, and where the next one starts.

    Attributes
    ----------
    records : tuple[CommitRecord, ...]
        The commits on this page.
    next_cursor : str or None
        The cursor for the following page, or None at the end.
    """

    records: tuple[CommitRecord, ...]
    next_cursor: str | None


class CommitAudit(typ.NamedTuple):
    """The outcome of reading a branch's commit authorship.

    Attributes
    ----------
    readable : bool
        Whether the API returned a commit list at all.
    foreign : tuple[ForeignCommit, ...]
        Commits Dependabot did not write.
    pages : int
        Commit pages fetched. Zero when the list could not be read.
    commits : int
        Commits judged across those pages.
    """

    readable: bool
    foreign: tuple[ForeignCommit, ...]
    pages: int = 0
    commits: int = 0


class ForeignCommit(typ.NamedTuple):
    """A commit on the branch that Dependabot did not write.

    Attributes
    ----------
    oid : str
        The commit SHA.
    author : str
        The author's login, or ``unknown`` when the API did not name one.
    """

    oid: str
    author: str

    def __str__(self) -> str:
        """Return a short description naming the commit and its author.

        Returns
        -------
        str
            ``<short sha> by <author>``.
        """
        return f"{self.oid[:8]} by {self.author}"


def commit_authors(commit: dict[str, JsonValue]) -> tuple[tuple[str, ...], bool]:
    """Return the logins credited on one commit, and whether that is all.

    A commit whose credit list is missing, malformed or empty yields one
    :data:`UNKNOWN_AUTHOR` rather than no authors at all. The difference
    decides the commit: an empty tuple has no login outside
    :data:`DEPENDABOT_LOGINS`, so the rule would find nothing to object
    to and certify a commit it has no evidence about. The whole point of
    the check is evidence, and absence of evidence is not evidence of
    Dependabot.

    Parameters
    ----------
    commit : dict
        The commit node from the GraphQL query.

    Returns
    -------
    tuple[tuple[str, ...], bool]
        The credited logins, and whether the connection returned every
        one. ``totalCount`` above the number of nodes means the credit
        list was cut off at the page size.

    Examples
    --------
    >>> commit_authors({"authors": {"totalCount": 0, "nodes": []}})
    (('an unnamed author',), True)
    """
    match commit.get("authors"):
        case {"nodes": list() as nodes} as authors:
            logins = [_login_of(node) for node in nodes] or [UNKNOWN_AUTHOR]
            total = authors.get("totalCount")
            complete = not isinstance(total, int) or total <= len(logins)
            return tuple(logins), complete
        case _:
            return (UNKNOWN_AUTHOR,), True


def _login_of(node: JsonValue) -> str:
    """Return the login one author node credits.

    Parameters
    ----------
    node : JsonValue
        One node of the author connection.

    Returns
    -------
    str
        The login, or :data:`UNKNOWN_AUTHOR` where GitHub named none.
    """
    match node:
        case {"user": {"login": str() as login}} if login:
            return login
        case _:
            return UNKNOWN_AUTHOR


def commit_page(pull_request: dict[str, JsonValue]) -> CommitPage | None:
    """Translate one page of the GraphQL commit connection.

    This is the whole of the GitHub shape's reach into the eligibility
    rule: everything past it reads :class:`CommitRecord`. Keeping the two
    apart is what lets the rule be stated and exercised without a GraphQL
    response, and what stops a schema change from quietly meaning a
    different rule.

    Parameters
    ----------
    pull_request : dict
        The pull request node from the GraphQL query.

    Returns
    -------
    CommitPage or None
        The commits on this page and the cursor for the next, or None
        when the response carried no commit list at all.
    """
    match pull_request.get("commits"):
        case {"nodes": list() as nodes} as commits:
            records = [
                record for node in nodes if (record := _commit_record(node)) is not None
            ]
            return CommitPage(records=tuple(records), next_cursor=next_cursor(commits))
        case _:
            return None


def _commit_record(node: JsonValue) -> CommitRecord | None:
    """Translate one node of the commit connection.

    Parameters
    ----------
    node : JsonValue
        One node of the commit connection.

    Returns
    -------
    CommitRecord or None
        The commit, or None where the node carried no commit at all.
    """
    match node:
        case {"commit": dict() as commit}:
            authors, complete = commit_authors(commit)
            oid = commit.get("oid")
            return CommitRecord(
                oid=oid if isinstance(oid, str) else UNKNOWN_AUTHOR,
                authors=authors,
                authors_complete=complete,
            )
        case _:
            return None


def next_cursor(commits: dict[str, JsonValue]) -> str | None:
    """Return the cursor for the next commit page, or None at the end.

    Parameters
    ----------
    commits : dict
        The commit connection from the GraphQL query.

    Returns
    -------
    str or None
        The end cursor when another page follows.
    """
    page_info = commits.get("pageInfo")
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    return cursor if isinstance(cursor, str) and cursor else None


def foreign_commits(records: typ.Sequence[CommitRecord]) -> tuple[ForeignCommit, ...]:
    """Apply the eligibility rule to commits already in domain terms.

    The rule: a commit must credit at least one author, every login
    credited on it must be Dependabot's, and the whole credit list must
    have been read. A commit whose authors
    came back truncated is reported foreign rather than waved through,
    because the check exists to certify the branch and a partial list
    certifies nothing. That is the opposite of the unreadable-list case
    in :func:`audit_commits`, and deliberately so: there the branch's
    commits could not be seen at all, which is a query fault affecting
    every consumer at once; here one visible commit could not be read to
    the end, which is a property of that commit.

    Parameters
    ----------
    records : typ.Sequence[CommitRecord]
        The branch's commits.

    Returns
    -------
    tuple[ForeignCommit, ...]
        One entry per commit that fails the rule, in branch order.
    """
    foreign: list[ForeignCommit] = []
    for record in records:
        if not record.authors:
            # No credited author is no evidence, and the rule certifies on
            # evidence. An empty tuple has no login outside
            # DEPENDABOT_LOGINS, so a rule that only looked for outsiders
            # would pass a commit it knows nothing about.
            foreign.append(ForeignCommit(oid=record.oid, author=UNKNOWN_AUTHOR))
            continue
        outside = [name for name in record.authors if name not in DEPENDABOT_LOGINS]
        if not outside:
            if record.authors_complete:
                continue
            foreign.append(ForeignCommit(oid=record.oid, author=UNREAD_CO_AUTHOR))
            continue
        foreign.append(ForeignCommit(oid=record.oid, author=outside[0]))
    return tuple(foreign)


def audit_commits(pull_request: dict[str, JsonValue]) -> CommitAudit:
    """Find the commits on one page that Dependabot did not write.

    Callers that must cover a whole branch use :func:`_fetch_pull_request`,
    which pages the connection first. This composes the adapter and the
    rule over a single response.

    Parameters
    ----------
    pull_request : dict
        The pull request node from the GraphQL query.

    Returns
    -------
    CommitAudit
        Whether the commit list could be read, and one entry per commit
        with an author outside :data:`DEPENDABOT_LOGINS`. An unreadable
        list yields no foreign commits, so the check fails open: a query
        change that stopped returning commits would otherwise halt every
        consumer's automerge at once, which is a worse failure than the
        one this prevents. ``readable`` is what makes that loss visible
        rather than silent.
    """
    page = commit_page(pull_request)
    if page is None:
        return CommitAudit(readable=False, foreign=())
    return CommitAudit(
        readable=True,
        foreign=foreign_commits(page.records),
        pages=1,
        commits=len(page.records),
    )
