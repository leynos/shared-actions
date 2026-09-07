"""Invariants of the eligibility rule, over branches nobody wrote down.

The behavioural suite drives the production path with branches chosen to
show one thing each. These state what must hold for every branch, which
catches the family a handful of examples cannot: a loop that stops at the
first offender, a branch that reports a commit twice, or one that skips
the commit after a match.

The rule is restated here rather than imported, so the two readings are
compared with each other rather than one reading with itself.
"""

from __future__ import annotations

from dependabot_graphql_double import DEPENDABOT, MAINTAINER
from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts import dependabot_commit_audit

_LOGINS = st.sampled_from([DEPENDABOT, "dependabot", MAINTAINER, "renovate[bot]", ""])


@st.composite
def _commit_records(
    draw: st.DrawFn,
) -> tuple[dependabot_commit_audit.CommitRecord, ...]:
    """Generate a branch's commits with arbitrary authorship.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis's draw function.

    Returns
    -------
    tuple[CommitRecord, ...]
        A bounded branch.
    """
    count = draw(st.integers(min_value=0, max_value=8))
    records: list[dependabot_commit_audit.CommitRecord] = []
    for index in range(count):
        logins = draw(st.lists(_LOGINS, min_size=0, max_size=4))
        complete = draw(st.booleans())
        authors = tuple(
            login or dependabot_commit_audit.UNKNOWN_AUTHOR for login in logins
        )
        records.append(
            dependabot_commit_audit.CommitRecord(
                oid=f"{index:040x}",
                authors=authors,
                authors_complete=complete,
            )
        )
    return tuple(records)


def _is_dependabot_only(record: dependabot_commit_audit.CommitRecord) -> bool:
    """Return whether one commit passes the eligibility rule.

    Stated here independently of the implementation, so the property
    tests compare two readings of the rule rather than one reading with
    itself. A commit crediting nobody fails: the rule certifies on
    evidence, and no credited author is no evidence.

    Parameters
    ----------
    record : dependabot_commit_audit.CommitRecord
        The commit to judge.

    Returns
    -------
    bool
        True when the commit is Dependabot's and wholly read.
    """
    if not record.authors:
        return False
    if not record.authors_complete:
        return False
    return all(
        author in dependabot_commit_audit.DEPENDABOT_LOGINS for author in record.authors
    )


class TestTheRuleHoldsOverArbitraryBranches:
    """Invariants of the rule, over branches nobody wrote down."""

    @given(records=_commit_records())
    def test_a_commit_is_reported_exactly_when_it_fails_the_rule(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The rule is total: every commit is judged, and judged once.

        Stating it as a property catches the family of defects a handful
        of examples cannot: a loop that stops at the first offender, a
        branch that reports a commit twice, or one that skips the commit
        after a match.
        """
        found = dependabot_commit_audit.foreign_commits(records)
        expected = [record.oid for record in records if not _is_dependabot_only(record)]
        assert [commit.oid for commit in found] == expected, (
            f"every failing commit must be reported once, in branch order; "
            f"got {[commit.oid for commit in found]} for {records}"
        )

    @given(records=_commit_records())
    def test_a_reported_commit_always_names_something(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The notice is the only record a maintainer sees.

        A blank author would leave the log saying a commit was rejected
        by nobody, which is unactionable.
        """
        for commit in dependabot_commit_audit.foreign_commits(records):
            assert commit.author, f"{commit.oid} was reported with no author"

    @given(
        records=_commit_records().filter(
            lambda records: all(_is_dependabot_only(record) for record in records)
        )
    )
    def test_a_wholly_dependabot_branch_is_never_reported(
        self, records: tuple[dependabot_commit_audit.CommitRecord, ...]
    ) -> None:
        """The gate must not stop the bumps it exists to let through.

        Both login variants count, in any mixture, on any number of
        commits. A commit crediting nobody is excluded: it is not a
        Dependabot commit, it is a commit with no evidence either way.
        """
        assert dependabot_commit_audit.foreign_commits(records) == (), (
            f"a branch written only by Dependabot must pass: {records}"
        )
