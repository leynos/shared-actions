"""What the auto-merge run decided.

The snapshot a decision is taken from and the rules that judge it. This
module writes nothing and calls nothing: every function here is a value
in and a value out, so a rule can be read as a rule and exercised
without a response, a runner or a captured stream. Saying the decision
out loud belongs to :mod:`dependabot_report`, and acting on it to
:mod:`dependabot_automerge`.
"""

from __future__ import annotations

import dataclasses

if __package__:
    from .dependabot_commit_audit import DEPENDABOT_LOGINS, ForeignCommit
    from .dependabot_merge_state import MergeableState, MergeStateStatus
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        DEPENDABOT_LOGINS,
        ForeignCommit,
    )
    from dependabot_merge_state import (  # type: ignore[import-not-found,no-redef]
        MergeableState,
        MergeStateStatus,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Snapshot of the pull request metadata used for gating.

    Attributes
    ----------
    number : int
        The pull request number.
    owner : str
        The repository owner (organization or user).
    repo : str
        The repository name.
    author : str
        The login of the pull request author.
    is_draft : bool
        Whether the pull request is a draft.
    labels : tuple[str, ...]
        Labels currently applied to the pull request.
    node_id : str or None
        The GraphQL node ID for mutations. None when created from event data.
    head_oid : str or None
        The head commit the audit read, named to every mutation so GitHub
        refuses to act on a head that moved since. None when created from
        event data, where no mutation follows.
    auto_merge_enabled : bool
        Whether auto-merge is already enabled on this PR.
    merge_state_status : MergeStateStatus
        The PR merge state status (e.g. CLEAN, UNSTABLE), or ``UNKNOWN``.
    mergeable_state : MergeableState
        The PR mergeable state (e.g. MERGEABLE, CONFLICTING), or ``UNKNOWN``.
    foreign_commits : tuple[ForeignCommit, ...]
        Commits on the branch that Dependabot did not write.
    commits_readable : bool
        Whether the commit list could be read. False means the check did
        not run and eligibility rests on the author alone.
    commit_pages_read : int
        How many pages of the commit connection were fetched. Reported so
        a run that read one page of a longer branch is distinguishable in
        the log from one that read the branch.
    commits_audited : int
        How many commits were judged.
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None = None
    head_oid: str | None = None
    auto_merge_enabled: bool = False
    merge_state_status: MergeStateStatus = MergeStateStatus.UNKNOWN
    mergeable_state: MergeableState = MergeableState.UNKNOWN
    foreign_commits: tuple[ForeignCommit, ...] = ()
    commits_readable: bool = True
    commit_pages_read: int = 0
    commits_audited: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """Decision describing whether auto-merge should proceed.

    Attributes
    ----------
    status : str
        The decision status: ``skipped``, ``cancelled``, ``ready``,
        ``enabled``, ``merged``, or ``error``. ``cancelled`` is a skip that
        also withdrew an auto-merge request armed earlier, and is reported
        separately because the run changed the pull request rather than
        merely declining to.
    reason : str
        Human-readable reason for the decision, e.g. ``author-not-dependabot``.
    """

    status: str
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class AutomergeConfig:
    """Configuration for emitting automerge decisions.

    Attributes
    ----------
    merge_method : str
        The normalized merge method (``SQUASH``, ``MERGE``, or ``REBASE``).
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.
    dry_run : bool
        If True, decisions are logged without calling the GitHub API.
    """

    merge_method: str
    required_label: str | None
    dry_run: bool


def armed_request_to_withdraw(pr: PullRequestContext) -> str | None:
    """Return the node of an auto-merge request that must be withdrawn.

    Three things have to hold at once, and each rules out a different
    case: the branch must carry a commit Dependabot did not write, a
    request must already be armed, and the pull request's node must be
    known so a mutation can name it. A branch skipped for any other
    reason keeps its request, since cancelling there would undo the
    arming this workflow exists to do.

    The node is returned rather than a flag so the caller has the value
    the mutation needs, and cannot ask for it a second way.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot the decision was taken from.

    Returns
    -------
    str or None
        The pull request's node id, or None when nothing is to be
        withdrawn.

    Examples
    --------
    >>> armed_request_to_withdraw(
    ...     PullRequestContext(
    ...         number=1,
    ...         owner="acme",
    ...         repo="example",
    ...         author="dependabot[bot]",
    ...         is_draft=False,
    ...         labels=(),
    ...     )
    ... ) is None
    True
    """
    if not pr.foreign_commits:
        return None
    if not pr.auto_merge_enabled:
        return None
    return pr.node_id


def evaluate(pr: PullRequestContext, required_label: str | None) -> Decision:
    """Evaluate a PR against eligibility rules and return a Decision.

    Dependabot eligibility accepts authors ``dependabot[bot]`` and
    ``dependabot`` as defined by :data:`DEPENDABOT_LOGINS`, which includes both
    author variants.

    Parameters
    ----------
    pr : PullRequestContext
        Snapshot of pull request metadata used for eligibility checks.
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.

    Returns
    -------
    Decision
        Outcome indicating whether auto-merge should proceed.

    Notes
    -----
    :data:`DEPENDABOT_LOGINS` is the canonical source of eligible Dependabot
    author logins used by this evaluation.

    """
    if pr.author not in DEPENDABOT_LOGINS:
        return Decision(status="skipped", reason="author-not-dependabot")
    if pr.foreign_commits:
        # Opening the pull request is not the same as writing what is in
        # it. Once Dependabot opens one, anything pushed to that branch
        # would otherwise merge under this rule without review, which is
        # how a workflow edit reached a trunk unreviewed.
        return Decision(
            status="skipped",
            reason=f"foreign-commit:{pr.foreign_commits[0].oid[:8]}",
        )
    if pr.is_draft:
        return Decision(status="skipped", reason="draft-pr")
    if required_label and required_label not in pr.labels:
        return Decision(status="skipped", reason=f"missing-label:{required_label}")
    return Decision(status="ready", reason="eligible")


def commit_audit_outcome(pr: PullRequestContext) -> str:
    """Return the commit audit's outcome as one of three fixed words.

    ``clean`` means every commit was read and every one was Dependabot's;
    ``foreign`` that at least one was not; ``unreadable`` that the check
    did not run. The value is bounded on purpose and carries no commit
    identifier, so the log line can be counted across repositories
    without becoming high-cardinality.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot the decision was taken from.

    Returns
    -------
    str
        ``clean``, ``foreign`` or ``unreadable``.
    """
    if not pr.commits_readable:
        return "unreadable"
    return "foreign" if pr.foreign_commits else "clean"
