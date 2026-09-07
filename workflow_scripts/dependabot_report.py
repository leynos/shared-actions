"""Saying out loud what the auto-merge run decided.

The GitHub Actions surface: the `key=value` lines a workflow reads back,
and the notices and warnings a maintainer reads. Separated from
:mod:`dependabot_decision`, which returns values and writes nothing, so
that the rules can be exercised without a captured stream and this
module can be changed without touching them.
"""

from __future__ import annotations

import typing as typ

if __package__:
    from .dependabot_decision import commit_audit_outcome
    from .output import emit
else:
    from dependabot_decision import (  # type: ignore[import-not-found,no-redef]
        commit_audit_outcome,
    )
    from output import emit  # type: ignore[import-not-found,no-redef]

if typ.TYPE_CHECKING:
    if __package__:
        from .dependabot_decision import (
            AutomergeConfig,
            Decision,
            PullRequestContext,
        )
    else:
        from dependabot_decision import (  # type: ignore[import-not-found,no-redef]
            AutomergeConfig,
            Decision,
            PullRequestContext,
        )


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
    # What the audit actually read, so a run that saw one page of a
    # longer branch is distinguishable from one that saw the branch.
    # Counts rather than identifiers, so the lines stay countable.
    emit("automerge_commit_pages_read", pr.commit_pages_read)
    emit("automerge_commits_audited", pr.commits_audited)
    if pr.foreign_commits:
        _announce_foreign_commits(pr)
    elif not pr.commits_readable:
        print(
            f"::warning title=dependabot-automerge::could not read the commits "
            f"of {pr.owner}/{pr.repo}#{pr.number}, so the commit-authorship "
            f"check did not run and eligibility rests on the pull request's "
            f"author alone. A change pushed onto this branch by someone other "
            f"than Dependabot would not be detected."
        )


def _announce_foreign_commits(pr: PullRequestContext) -> None:
    """Name the commits that stopped the branch merging unattended."""
    named = ", ".join(str(commit) for commit in pr.foreign_commits)
    print(
        f"::notice title=dependabot-automerge::{pr.owner}/{pr.repo}#{pr.number} "
        f"carries {len(pr.foreign_commits)} commit(s) Dependabot did not write "
        f"({named}), so it will not merge unattended. A change pushed onto a "
        f"Dependabot branch needs its own pull request and its own review."
    )
