"""What the auto-merge run decided, and how it says so.

The snapshot a decision is taken from, the rules that judge it, and the
structured lines the workflow reads back. Kept apart from the fetching
and the API calls, so the rules can be read as rules and exercised
against a value rather than a response.
"""

from __future__ import annotations

import dataclasses

if __package__:
    from .dependabot_commit_audit import DEPENDABOT_LOGINS, ForeignCommit
    from .dependabot_merge_state import MergeableState, MergeStateStatus
    from .output import emit
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        DEPENDABOT_LOGINS,
        ForeignCommit,
    )
    from dependabot_merge_state import (  # type: ignore[import-not-found,no-redef]
        MergeableState,
        MergeStateStatus,
    )
    from output import emit  # type: ignore[import-not-found,no-redef]


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
    """

    number: int
    owner: str
    repo: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    node_id: str | None = None
    auto_merge_enabled: bool = False
    merge_state_status: MergeStateStatus = MergeStateStatus.UNKNOWN
    mergeable_state: MergeableState = MergeableState.UNKNOWN
    foreign_commits: tuple[ForeignCommit, ...] = ()
    commits_readable: bool = True


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


def emit_decision(
    pr: PullRequestContext,
    decision: Decision,
    *,
    config: AutomergeConfig,
) -> None:
    """Emit structured decision output for the automerge workflow."""
    reason = decision.reason
    if decision.status == "ready" and config.dry_run:
        status = "dry-run"
    else:
        status = decision.status
    emit("automerge_status", status)
    emit("automerge_reason", reason)
    emit("automerge_merge_method", config.merge_method)
    emit("automerge_required_label", config.required_label or "")
    emit("automerge_repository", f"{pr.owner}/{pr.repo}")
    emit("automerge_pr_number", pr.number)
    emit("automerge_author", pr.author)
    emit("automerge_draft", str(pr.is_draft).lower())
    emit("automerge_labels", pr.labels)
    emit("automerge_merge_state", pr.merge_state_status.value)
    emit("automerge_mergeable_state", pr.mergeable_state.value)
    emit("automerge_commit_audit", commit_audit_outcome(pr))
    if pr.foreign_commits:
        _announceforeign_commits(pr)
    elif not pr.commits_readable:
        print(
            f"::warning title=dependabot-automerge::could not read the commits "
            f"of {pr.owner}/{pr.repo}#{pr.number}, so the commit-authorship "
            f"check did not run and eligibility rests on the pull request's "
            f"author alone. A change pushed onto this branch by someone other "
            f"than Dependabot would not be detected."
        )


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


def _announceforeign_commits(pr: PullRequestContext) -> None:
    """Name the commits that stopped the branch merging unattended."""
    named = ", ".join(str(commit) for commit in pr.foreign_commits)
    print(
        f"::notice title=dependabot-automerge::{pr.owner}/{pr.repo}#{pr.number} "
        f"carries {len(pr.foreign_commits)} commit(s) Dependabot did not write "
        f"({named}), so it will not merge unattended. A change pushed onto a "
        f"Dependabot branch needs its own pull request and its own review."
    )
