"""What the workflow does about who wrote the commits on a branch.

The author field names who opened the pull request, not who wrote what
is on it, so a maintainer pushing to a Dependabot branch would be merged
unattended without an audit. These tests read that audit out of a
scripted GitHub and check what the workflow does with each answer.

Separated from `test_dependabot_automerge`, whose subject is the command
line, the option normalisation and the merge-state retry. The two ask
different questions of the same script, and a module that asked both
answered neither clearly.
"""

from __future__ import annotations

import typing as typ

import pytest

from workflow_scripts import (
    dependabot_automerge,
    dependabot_commit_audit,
    dependabot_decision,
    dependabot_report,
)


class PullRequestOverrides(typ.TypedDict, total=False):
    """The fields :func:`_pr` accepts, with their real types.

    A ``dict[str, typ.Any]`` here would take a misspelled field name or a
    wrongly typed value from every caller without Pyright noticing, which
    is the opposite of what a fixture builder is for.
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None
    auto_merge_enabled: bool
    merge_state_status: dependabot_automerge.MergeStateStatus
    mergeable_state: dependabot_automerge.MergeableState
    foreign_commits: tuple[dependabot_commit_audit.ForeignCommit, ...]
    commits_readable: bool


def _pr(
    **overrides: typ.Unpack[PullRequestOverrides],
) -> dependabot_decision.PullRequestContext:
    """Build a PullRequestContext with eligible defaults."""
    values: PullRequestOverrides = {
        "number": 1,
        "owner": "leynos",
        "repo": "rstest-bdd",
        "author": "dependabot[bot]",
        "is_draft": False,
        "labels": (),
    }
    values.update(overrides)
    return dependabot_decision.PullRequestContext(**values)


def _commit_node(oid: str, *logins: str) -> dict[str, object]:
    """Build one commit node as the GraphQL query returns it.

    ``totalCount`` is included because the query asks for it and GitHub
    always answers it. Omitting it here would model a response that does
    not occur, and would exercise the refusal path in every case rather
    than the rule the case is about.
    """
    nodes = [{"user": {"login": login}} for login in logins]
    return {
        "commit": {
            "oid": oid,
            "authors": {"totalCount": len(nodes), "nodes": nodes},
        }
    }


class TestForeignCommitExtraction:
    """Reading who wrote each commit on the branch."""

    def test_an_empty_connection_counts_as_read(self) -> None:
        """Zero authors and a count of zero agree with each other.

        The count is compared against the nodes rather than the credited
        logins, because a commit crediting nobody still yields one
        placeholder login and would otherwise read as a list cut short.
        The distinction never changes the verdict, since the placeholder
        is not a Dependabot login and fails the rule either way, so it is
        asserted on the reading rather than through the verdict.
        """
        _, complete = dependabot_commit_audit.commit_authors(
            {"authors": {"totalCount": 0, "nodes": []}}
        )

        assert complete, (
            "a connection returning no authors and counting none is "
            "consistent, so the credit list was read to the end"
        )

    def test_a_commit_crediting_nobody_names_that_rather_than_truncation(
        self,
    ) -> None:
        """A count of zero over no nodes is honest, not truncated.

        The commit still fails, because the rule certifies on evidence
        and no credited author is no evidence. What it must not do is
        blame the page size: the notice a maintainer reads names the
        unnamed author, and the count is compared against the nodes
        rather than the credited logins so that an empty connection
        reads as consistent rather than as a list cut short.
        """
        payload = {
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "oid": "aaaa1111",
                            "authors": {"totalCount": 0, "nodes": []},
                        }
                    }
                ]
            }
        }

        (foreign,) = dependabot_commit_audit.audit_commits(payload).foreign

        assert foreign.author == dependabot_commit_audit.UNKNOWN_AUTHOR, (
            "a commit crediting nobody must be reported as unauthored, not "
            f"as an unread list; got {foreign.author!r}"
        )

    @pytest.mark.parametrize(
        ("authors", "reason"),
        [
            pytest.param(
                {"nodes": [{"user": {"login": "dependabot[bot]"}}]},
                "no totalCount at all",
                id="the-count-is-absent",
            ),
            pytest.param(
                {"totalCount": None, "nodes": [{"user": {"login": "dependabot"}}]},
                "a null count",
                id="the-count-is-null",
            ),
            pytest.param(
                {"totalCount": "1", "nodes": [{"user": {"login": "dependabot"}}]},
                "a count that is not a number",
                id="the-count-is-a-string",
            ),
            pytest.param(
                {"totalCount": True, "nodes": [{"user": {"login": "dependabot"}}]},
                "a boolean, which is an int in Python",
                id="the-count-is-a-boolean",
            ),
            pytest.param(
                {"totalCount": 0, "nodes": [{"user": {"login": "dependabot"}}]},
                "a count below the nodes it returned",
                id="the-count-is-below-the-nodes",
            ),
            pytest.param(
                {"totalCount": 3, "nodes": [{"user": {"login": "dependabot"}}]},
                "a count above the nodes, the paged case",
                id="the-count-is-above-the-nodes",
            ),
        ],
    )
    def test_a_count_that_does_not_account_for_the_nodes_is_foreign(
        self, authors: dict[str, object], reason: str
    ) -> None:
        """Every commit here credits Dependabot and none of them may merge.

        The logins alone would pass the rule. What fails is the evidence:
        a connection whose own count does not describe the nodes it
        returned certifies nothing, and this check exists to certify the
        branch. Treating any of these as complete would let an unattended
        merge proceed on an authorship list that was never read to the
        end.
        """
        payload = {
            "commits": {
                "nodes": [{"commit": {"oid": "aaaa1111", **{"authors": authors}}}]
            }
        }

        foreign = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.oid for commit in foreign] == ["aaaa1111"], (
            f"{reason} must be refused rather than certified"
        )

    def test_an_all_dependabot_branch_has_none(self) -> None:
        """The ordinary bump, which must keep merging unattended."""
        payload = {
            "commits": {
                "nodes": [
                    _commit_node("aaaaaaaa1111", "dependabot[bot]"),
                    _commit_node("bbbbbbbb2222", "dependabot"),
                ]
            }
        }

        assert dependabot_commit_audit.audit_commits(payload).foreign == (), (
            "both logins are Dependabot's, so nothing is foreign"
        )

    def test_a_maintainer_commit_is_foreign(self) -> None:
        """The case this exists for: a human fix pushed onto the branch."""
        payload = {
            "commits": {
                "nodes": [
                    _commit_node("aaaaaaaa1111", "dependabot[bot]"),
                    _commit_node("cccccccc3333", "leynos"),
                ]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.oid for commit in found] == ["cccccccc3333"], found
        assert found[0].author == "leynos", found

    def test_a_co_authored_commit_is_foreign(self) -> None:
        """Dependabot plus a human is still a human's change.

        A commit crediting both would pass a check that asked only
        whether Dependabot appears among the authors.
        """
        payload = {
            "commits": {
                "nodes": [_commit_node("dddd4444", "dependabot[bot]", "leynos")]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.author for commit in found] == ["leynos"], found

    def test_an_unnamed_author_is_foreign(self) -> None:
        """A commit GitHub cannot attribute is not evidence of a bot."""
        payload = {
            "commits": {
                "nodes": [{"commit": {"oid": "eeee5555", "authors": {"nodes": [{}]}}}]
            }
        }

        found = dependabot_commit_audit.audit_commits(payload).foreign

        assert [commit.author for commit in found] == [
            dependabot_commit_audit.UNKNOWN_AUTHOR
        ], found

    @pytest.mark.parametrize(
        "payload",
        [{}, {"commits": None}, {"commits": {"nodes": None}}],
        ids=["absent", "null-commits", "null-nodes"],
    )
    def test_an_unreadable_commit_list_blocks_nothing(
        self, payload: dict[str, object]
    ) -> None:
        """Unknown is not the same as foreign.

        A query change that stopped returning commits would otherwise
        halt every consumer's automerge at once, which is a worse
        failure than the one this check prevents.
        """
        audit = dependabot_commit_audit.audit_commits(payload)

        assert audit.foreign == (), payload
        assert not audit.readable, (
            "an unreadable list must be distinguishable from a clean branch, "
            "or the loss of the check is silent"
        )

    def test_a_readable_list_says_so(self) -> None:
        """The ordinary case must not look like a failure to read."""
        payload = {"commits": {"nodes": [_commit_node("aaaa1111", "dependabot[bot]")]}}

        assert dependabot_commit_audit.audit_commits(payload).readable, payload


class TestForeignCommitsBlockAutomerge:
    """The verdict, and what it tells the maintainer."""

    def test_a_clean_branch_stays_eligible(self) -> None:
        """The ordinary bump is unaffected."""
        decision = dependabot_decision.evaluate(_pr(), None)

        assert decision.status == "ready", decision
        assert decision.reason == "eligible", decision

    def test_a_foreign_commit_skips_and_names_it(self) -> None:
        """Opening a pull request is not the same as writing what is in it."""
        pr = _pr(
            foreign_commits=(
                dependabot_commit_audit.ForeignCommit("cccccccc3333", "leynos"),
            )
        )

        decision = dependabot_decision.evaluate(pr, None)

        assert decision.status == "skipped", decision
        assert decision.reason == "foreign-commit:cccccccc", decision

    def test_the_annotation_names_the_commit_and_the_remedy(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A maintainer must learn why, not merely that it stopped."""
        pr = _pr(
            foreign_commits=(
                dependabot_commit_audit.ForeignCommit("cccccccc3333", "leynos"),
            )
        )

        dependabot_report.emit_decision(
            pr,
            dependabot_decision.Decision(
                status="skipped", reason="foreign-commit:cccccccc"
            ),
            config=dependabot_decision.AutomergeConfig(
                merge_method="squash", required_label=None, dry_run=False
            ),
        )

        out = capsys.readouterr().out
        assert "::notice title=dependabot-automerge::" in out, out
        assert "cccccccc by leynos" in out, out
        assert "its own pull request" in out, out


def test_an_unreadable_commit_list_is_announced(
    capsys: pytest.CaptureFixture,
) -> None:
    """Failing open must be loud.

    The check is allowed to pass a branch it cannot inspect, because
    refusing on unknown would stop every consumer's automerge at once.
    What it may not do is lose the protection quietly.
    """
    dependabot_report.emit_decision(
        _pr(commits_readable=False),
        dependabot_decision.Decision(status="ready", reason="eligible"),
        config=dependabot_decision.AutomergeConfig(
            merge_method="squash", required_label=None, dry_run=False
        ),
    )

    out = capsys.readouterr().out
    assert "::warning title=dependabot-automerge::" in out, out
    assert "did not run" in out, out
    assert "author alone" in out, out
