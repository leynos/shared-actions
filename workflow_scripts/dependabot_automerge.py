#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts>=3.24,<4.0", "httpx>=0.28,<0.29"]
# ///

"""Enable GitHub auto-merge for eligible Dependabot pull requests.

This script evaluates pull requests against eligibility rules and enables
GitHub's auto-merge feature for qualifying Dependabot PRs. It is designed
to run in GitHub Actions workflows via `workflow_call`.

Eligibility Rules
-----------------
Auto-merge is enabled only when all conditions are met:

- The PR author is ``dependabot[bot]`` or ``dependabot``
- The PR is not a draft
- The required label (default: ``dependencies``) is present
- Every commit the audit could read is Dependabot's

An unreadable commit list does not block eligibility. :func:`evaluate`
never consults ``commits_readable``: refusing on unknown would stop
every consumer's auto-merge the moment a query change stopped returning
commits, which is a worse failure than the one being prevented. The run
fails open and says so, and :func:`emit_decision` prints a warning
naming what did not run.

Commit Audit
------------
The author field names who opened the pull request, not who wrote what
is on the branch, so a maintainer pushing to a Dependabot branch would
otherwise be merged unattended. Every commit is therefore audited.

The commit connection is paged to the end rather than read once: a
connection read to its page size looks complete, and a foreign commit
past that limit would be certified as Dependabot's. A branch longer
than the page cap, or a page carrying no commit list, is reported
unreadable rather than clean, because the check exists to certify the
branch and a partial read certifies nothing. An author list the
connection could not account for is reported foreign instead: the audit
reached that commit, so the incomplete credit is a fact about the
commit rather than a gap in the branch read, and
``dependabot_commit_audit`` fails closed on it.

A foreign commit does more than withhold a merge. Auto-merge already
armed on the pull request is withdrawn, since arming it was a decision
taken while the branch was still Dependabot's and GitHub keeps such a
request alive across a push. The withdrawal is deliberately limited to
that case: a branch skipped for any other reason, an unreadable audit
among them, keeps its request, because cancelling there would undo the
arming this workflow exists to do. Both mutations are bound to the head
commit the audit read, so a push racing the run cannot have the decision
applied to it.

The rule itself lives in ``dependabot_commit_audit`` and takes values
rather than a client, so it can be exercised without a network. The
GitHub boundary and the branch audit live in ``dependabot_github``,
whose readers require the GraphQL call as an argument and have no
default for it. Every read and every mutation on the live path takes
the same call, threaded down from :func:`main`, which is the one place
that names ``request_graphql``. Nothing below that boundary reaches for
a client of its own, so a test supplies one call and covers the initial
read, the retry refreshes, the withdrawal and the merge alike.

Merge-state handling: auto-merge is armed while the PR is blocked by
required rules (``BLOCKED``). If the PR is already mergeable (``CLEAN``,
``HAS_HOOKS``, or ``UNSTABLE``), GitHub rejects enabling auto-merge, so the
PR is merged directly instead — matching what auto-merge itself would do,
because all required rules are already satisfied. Repositories should
configure required status checks (branch protection or rulesets) so that CI
genuinely gates merging.

Environment Variables
---------------------
INPUT_GITHUB_TOKEN : str
    GitHub token with ``contents:write`` and ``pull-requests:write`` permissions.
INPUT_MERGE_METHOD : str, optional
    Merge method: ``squash``, ``merge``, or ``rebase``. Default: ``squash``.
INPUT_REQUIRED_LABEL : str, optional
    Label required on the PR. Default: ``dependencies``.
INPUT_DRY_RUN : bool, optional
    If ``true``, logs the decision without calling the GitHub API.
INPUT_PULL_REQUEST_NUMBER : int, optional
    PR number override for workflow_call contexts.
INPUT_REPOSITORY : str, optional
    Repository override in ``owner/repo`` form.

Usage
-----
As a standalone script::

    INPUT_GITHUB_TOKEN=ghp_... uv run dependabot_automerge.py

In a GitHub Actions workflow::

    - uses: ./.github/workflows/dependabot-automerge.yml
      with:
        pull-request-number: ${{ github.event.pull_request.number }}

Side Effects
------------
When not in dry-run mode, this script calls the GitHub GraphQL API to enable
auto-merge on the target pull request, or to merge it directly when it is
already in a mergeable state.

See Also
--------
main : The CLI entrypoint function.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import typing as typ
from pathlib import Path
from types import MappingProxyType

from cyclopts import App, Parameter

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    import collections.abc as cabc

if __package__:
    from .dependabot_commit_audit import (
        CommitAudit,
        CommitRecord,
        DependabotLogin,
        ForeignCommit,
    )
    from .dependabot_decision import (
        AutomergeConfig,
        Decision,
        DecisionStatus,
        MergeMethod,
        PullRequestContext,
        evaluate,
        judge,
    )
    from .dependabot_github import (
        GraphQLQuery,
        PullRequestRef,
        fetch_pull_request,
    )
    from .dependabot_merge_state import (
        MergeStateRetryConfig,
        classify_merge_state,
        merge_state_retry_config,
    )
    from .dependabot_metrics import Operation, measured
    from .dependabot_queries import (
        DISABLE_AUTOMERGE_MUTATION,
        ENABLE_AUTOMERGE_MUTATION,
        MERGE_PULL_REQUEST_MUTATION,
    )
    from .dependabot_report import emit_decision, emit_withdrawal_notice
    from .graphql_client import JsonValue, request_graphql
    from .output import fail
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        CommitAudit,
        CommitRecord,
        DependabotLogin,
        ForeignCommit,
    )
    from dependabot_decision import (  # type: ignore[import-not-found,no-redef]
        AutomergeConfig,
        Decision,
        DecisionStatus,
        MergeMethod,
        PullRequestContext,
        evaluate,
        judge,
    )
    from dependabot_github import (  # type: ignore[import-not-found,no-redef]
        GraphQLQuery,
        PullRequestRef,
        fetch_pull_request,
    )
    from dependabot_merge_state import (  # type: ignore[import-not-found,no-redef]
        MergeStateRetryConfig,
        classify_merge_state,
        merge_state_retry_config,
    )
    from dependabot_metrics import (  # type: ignore[import-not-found,no-redef]
        Operation,
        measured,
    )
    from dependabot_queries import (  # type: ignore[import-not-found,no-redef]
        DISABLE_AUTOMERGE_MUTATION,
        ENABLE_AUTOMERGE_MUTATION,
        MERGE_PULL_REQUEST_MUTATION,
    )
    from dependabot_report import (  # type: ignore[import-not-found,no-redef]
        emit_decision,
        emit_withdrawal_notice,
    )
    from graphql_client import (  # type: ignore[import-not-found,no-redef]
        JsonValue,
        request_graphql,
    )
    from output import fail  # type: ignore[import-not-found,no-redef]


#: Re-exported so the workflow module remains the single import for
#: callers and tests that do not care where the audit lives.
__all__ = [
    "CommitAudit",
    "CommitRecord",
    "DependabotLogin",
    "ForeignCommit",
    "main",
]


#: The workflow's input spelling for each merge method. Derived from
#: `MergeMethod` rather than restated, so a member added there cannot be
#: silently unreachable from the input.
MERGE_METHODS: cabc.Mapping[str, MergeMethod] = MappingProxyType(
    {method.value.lower(): method for method in MergeMethod}
)

app = App()


@dataclasses.dataclass(frozen=True, slots=True)
class LiveRun:
    """Everything a live run acts through, as one value.

    The token, the GraphQL call and the run's configuration travel
    together from :func:`main` to every read and every mutation, so
    carrying them as three parameters made each function along the way
    restate the same trio. One value keeps the composition root the only
    place that names a client, without that cost.

    Attributes
    ----------
    token : str
        A GitHub token.
    query : GraphQLQuery
        The one GraphQL call this run uses, chosen at the composition
        root. Nothing below it resolves a client of its own.
    config : AutomergeConfig
        The run's configuration, including the normalized merge method.
    """

    token: str
    query: GraphQLQuery
    config: AutomergeConfig


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Runtime context for PR evaluation.

    Attributes
    ----------
    repo_full_name : str
        The full repository name in ``owner/repo`` format.
    event : dict or None
        The parsed GitHub event payload, or None if unavailable.
    pull_request_number : int or None
        Explicit PR number override, or None to resolve from event.
    """

    repo_full_name: str
    event: dict[str, JsonValue] | None
    pull_request_number: int | None


def _load_event() -> dict[str, JsonValue] | None:
    """Load the GitHub event payload from GITHUB_EVENT_PATH if set."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Failed to parse event payload: {exc}")


def _normalize_label(required_label: str | None) -> str | None:
    """Strip whitespace from a label, returning None if empty."""
    if required_label is None:
        return None
    label = required_label.strip()
    return label or None


def _normalize_merge_method(merge_method: str) -> MergeMethod:
    """Validate a workflow input and return the merge method it names."""
    normalized = merge_method.strip().lower()
    if normalized not in MERGE_METHODS:
        allowed = ", ".join(sorted(MERGE_METHODS))
        fail(f"Invalid merge_method '{merge_method}'. Allowed: {allowed}.")
    return MERGE_METHODS[normalized]


def _get_repo_from_event(event: dict[str, JsonValue] | None) -> str:
    """Return the repository full name from an event payload when available."""
    if not event:
        return ""
    repository = event.get("repository")
    if not isinstance(repository, dict):
        return ""
    full_name = repository.get("full_name")
    if isinstance(full_name, str):
        return full_name.strip()
    return ""


def _resolve_repository(
    repository: str | None, event: dict[str, JsonValue] | None
) -> str:
    """Resolve the repository from input, event payload, or environment."""
    if repository and (candidate := repository.strip()):
        return candidate
    if repo := _get_repo_from_event(event):
        return repo
    if repo := os.environ.get("GITHUB_REPOSITORY"):
        return repo
    fail("Repository not provided. Set INPUT_REPOSITORY or GITHUB_REPOSITORY.")
    raise AssertionError  # unreachable


def _split_repo(full_name: str) -> tuple[str, str]:
    """Split a full repository name into owner and repo components."""
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        fail(f"Repository '{full_name}' must be in owner/repo form.")
    return parts[0], parts[1]


def _resolve_pull_request_number(
    pull_request_number: int | None, event: dict[str, JsonValue] | None
) -> int:
    """Resolve the PR number from input or event payload."""
    if pull_request_number is not None:
        return pull_request_number
    if event and (number := event.get("pull_request", {}).get("number")) is not None:
        try:
            return int(number)
        except (TypeError, ValueError):
            pass
    fail(
        "Pull request number not provided. Set INPUT_PULL_REQUEST_NUMBER or include "
        "it in the event payload."
    )
    raise AssertionError  # unreachable


def _parse_pr_number(pr: dict[str, JsonValue]) -> int:
    """Parse and validate the pull request number from event payload data."""
    number = pr.get("number")
    if number is None:
        fail("Event payload missing pull_request.number.")
    try:
        return int(number)
    except (TypeError, ValueError):
        fail("Event payload pull_request.number is not an integer.")


def _labels_from_pr(pr: dict[str, JsonValue]) -> tuple[str, ...]:
    """Extract label names from a validated pull_request dict."""
    labels = []
    for label in pr.get("labels") or []:
        if not isinstance(label, dict):
            continue
        name = label.get("name")
        if isinstance(name, str) and name:
            labels.append(name)
    return tuple(labels)


def _snapshot_from_event(
    event: dict[str, JsonValue], repo_full_name: str
) -> PullRequestContext:
    """Build a PullRequestContext from a GitHub event payload.

    The event carries no commit list, so the audit did not run here and
    the context says so. Leaving ``commits_readable`` at its default
    would have the dry run report ``automerge_commit_audit=clean``, which
    claims a check that was never made and does so in the one output
    meant to be counted.
    """
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        fail("Event payload does not include pull_request data.")
    pr_number = _parse_pr_number(pr)
    author = pr.get("user", {}).get("login")
    author_login = author if isinstance(author, str) else ""
    is_draft = bool(pr.get("draft", False))
    owner, repo = _split_repo(repo_full_name)
    return PullRequestContext(
        number=pr_number,
        owner=owner,
        repo=repo,
        author=author_login,
        is_draft=is_draft,
        labels=_labels_from_pr(pr),
        commits_readable=False,
    )


def _refresh_merge_state(
    pr: PullRequestContext,
    *,
    run: LiveRun,
    retry: MergeStateRetryConfig | None = None,
) -> PullRequestContext:
    """Refresh PR merge state, retrying while mergeability is unknown.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot to refresh.
    run : LiveRun
        The run this refresh belongs to.
    retry : MergeStateRetryConfig or None
        Retry bounds, or None to read them from the environment.

    Returns
    -------
    PullRequestContext
        The latest snapshot, which may still be unknown.
    """
    current = pr
    retry_config = retry if retry is not None else merge_state_retry_config()
    # Read back by `measured` after the body. One attempt is the happy
    # path: the first classification was not `retry` and nothing was
    # refetched. How often this exceeds one is the whole reason the
    # refresh has a metric.
    reads = 1

    def _reads() -> int:
        return reads

    with measured(Operation.REFRESH, attempts=_reads):
        for attempt in range(retry_config.max_attempts):
            state, _reason = classify_merge_state(
                current.merge_state_status,
                current.mergeable_state,
            )
            if state != "retry":
                return current
            sleep_seconds = min(
                retry_config.base_sleep * (2**attempt),
                retry_config.max_sleep,
            )
            time.sleep(sleep_seconds)
            reads += 1
            current = fetch_pull_request(
                run.token,
                PullRequestRef(
                    owner=current.owner, repo=current.repo, number=current.number
                ),
                query=run.query,
            )
        return current


def _disable_automerge(pull_request_id: str, *, run: LiveRun) -> None:
    """Cancel an auto-merge request via the GitHub GraphQL API.

    Parameters
    ----------
    pull_request_id : str
        The node ID of the pull request whose request is withdrawn.
    run : LiveRun
        The run this withdrawal belongs to.
    """
    with measured(Operation.DISABLE):
        run.query(
            run.token,
            DISABLE_AUTOMERGE_MUTATION,
            {"pullRequestId": pull_request_id},
        )


def _mutate(pr: PullRequestContext, mutation: str, *, run: LiveRun) -> None:
    """Send one merge mutation, bound to the head the audit read.

    ``expectedHeadOid`` is what makes the audit binding rather than
    advisory. A push can land between reading the commits and arming or
    performing the merge, and GitHub makes no head-match check without
    it, so the request would be armed on a head nobody looked at. Where
    the head has moved GitHub refuses the mutation, which fails the run:
    no merge happens, and the push that moved the head starts a new run
    that audits it from scratch.

    Parameters
    ----------
    pr : PullRequestContext
        The audited snapshot.
    mutation : str
        The GraphQL document to send. ``ENABLE_AUTOMERGE_MUTATION``
        arms the request; ``MERGE_PULL_REQUEST_MUTATION`` merges now.
        The two differ only in the document, so they share this body
        rather than each having a wrapper of its own.
    run : LiveRun
        The run this mutation belongs to.
    """
    if not pr.node_id:
        fail("Pull request node ID missing from GitHub response.")
    if not pr.head_oid:
        fail(
            f"Pull request {pr.owner}/{pr.repo}#{pr.number} reported no head "
            f"commit, so the merge cannot be bound to the head the commit "
            f"audit read. Refusing to act on an unaudited head."
        )
    # Named from the document rather than reported as one, because the
    # two callers differ only in that argument and a shared metric would
    # make arming and merging indistinguishable in the log.
    operation = (
        Operation.MERGE if mutation is MERGE_PULL_REQUEST_MUTATION else Operation.ENABLE
    )
    with measured(operation):
        run.query(
            run.token,
            mutation,
            {
                "pullRequestId": pr.node_id,
                "mergeMethod": run.config.merge_method.value,
                "expectedHeadOid": pr.head_oid,
            },
        )


def _handle_dry_run(
    event: dict[str, JsonValue] | None,
    repo_full_name: str,
    *,
    config: AutomergeConfig,
) -> None:
    """Handle the dry-run execution path without API calls."""
    if event is None:
        fail("Dry-run mode requires GITHUB_EVENT_PATH with pull_request data.")
    snapshot = _snapshot_from_event(event, repo_full_name)
    decision = evaluate(snapshot, config.required_label)
    emit_decision(
        snapshot,
        decision,
        config=config,
    )


def _stop_unless_eligible(pr: PullRequestContext, *, run: LiveRun) -> bool:
    """Carry out the judgement, and stop when the branch is ineligible.

    Three things happen here and none of them is decided here. `judge`
    in :mod:`dependabot_decision` works out both the decision and whether
    a request should be withdrawn, and executes nothing.
    `_disable_automerge` performs the withdrawal through the injected
    call and decides nothing. `emit_withdrawal_notice` and
    `emit_decision` in :mod:`dependabot_report` say what happened. What
    is left here is the order the three go in, which is the only part
    that is genuinely about running.

    Withdrawal matters because declining to arm a request is not enough
    on its own: GitHub keeps an existing request alive across a push, so
    one armed while the branch was still Dependabot's would merge the
    commit that made it foreign as soon as the required checks passed.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot to judge.
    run : LiveRun
        The run this judgement belongs to.

    Returns
    -------
    bool
        True when the caller must stop.
    """
    judgement = judge(pr, run.config.required_label)
    if judgement.decision.status is DecisionStatus.READY:
        return False
    decision = judgement.decision
    if judgement.withdraw is not None:
        _disable_automerge(judgement.withdraw, run=run)
        emit_withdrawal_notice(pr)
        decision = Decision(status=DecisionStatus.CANCELLED, reason=decision.reason)
    emit_decision(pr, decision, config=run.config)
    return True


def _report(
    pr: PullRequestContext, status: DecisionStatus, reason: str, *, run: LiveRun
) -> None:
    """Say one decision out loud and stop there.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot the decision is about.
    status : DecisionStatus
        The ``automerge_status`` value.
    reason : str
        The ``automerge_reason`` value.
    run : LiveRun
        The run this decision belongs to.
    """
    emit_decision(pr, Decision(status=status, reason=reason), config=run.config)


def _run_is_over(pr: PullRequestContext, *, run: LiveRun) -> bool:
    """Report and return True when this snapshot ends the run.

    A snapshot ends the run when the pull request is not eligible, or
    when auto-merge is already armed on it and there is nothing to do.

    Parameters
    ----------
    pr : PullRequestContext
        The snapshot to judge.
    run : LiveRun
        The run this judgement belongs to.

    Returns
    -------
    bool
        True when the caller must stop, having reported why.
    """
    if _stop_unless_eligible(pr, run=run):
        return True
    if pr.auto_merge_enabled:
        _report(pr, DecisionStatus.ENABLED, "already-enabled", run=run)
        return True
    return False


def _read_snapshot(context: RuntimeContext, *, run: LiveRun) -> PullRequestContext:
    """Read the pull request once, and do nothing else with it.

    A read, and only a read. It judges nothing, reports nothing and
    withdraws nothing, so a caller can obtain a snapshot without
    thereby having acted on one. The version this replaces returned an
    optional snapshot and reached the `None` by reporting a decision
    and, on one path, withdrawing an armed request: a name that
    promised a value and delivered consequences.

    Parameters
    ----------
    context : RuntimeContext
        Where the run is, and which pull request it is about.
    run : LiveRun
        The run this read belongs to.

    Returns
    -------
    PullRequestContext
        The snapshot, whatever it says.
    """
    owner, repo = _split_repo(context.repo_full_name)
    pr_number = _resolve_pull_request_number(
        context.pull_request_number,
        context.event,
    )
    with measured(Operation.LOOKUP):
        return fetch_pull_request(
            run.token,
            PullRequestRef(owner=owner, repo=repo, number=pr_number),
            query=run.query,
        )


def _act_on_merge_state(pr: PullRequestContext, *, run: LiveRun) -> None:
    """Arm auto-merge, merge outright, or skip, on the merge state.

    Parameters
    ----------
    pr : PullRequestContext
        The eligible snapshot, carrying the head the audit read.
    run : LiveRun
        The run this action belongs to.
    """
    state, reason = classify_merge_state(
        pr.merge_state_status,
        pr.mergeable_state,
    )
    if state in {"skip", "retry"}:
        skipped = reason if state == "skip" and reason else "merge-state-unknown"
        _report(pr, DecisionStatus.SKIPPED, skipped, run=run)
        return
    if state == "merge":
        _mutate(pr, MERGE_PULL_REQUEST_MUTATION, run=run)
        _report(pr, DecisionStatus.MERGED, "merged-directly", run=run)
        return
    _mutate(pr, ENABLE_AUTOMERGE_MUTATION, run=run)
    _report(pr, DecisionStatus.ENABLED, "enabled", run=run)


def _handle_live_execution(context: RuntimeContext, *, run: LiveRun) -> None:
    """Handle the live execution path that talks to the GitHub API.

    Parameters
    ----------
    context : RuntimeContext
        Where the run is, and which pull request it is about.
    run : LiveRun
        The run, carrying the token, the one GraphQL call every read and
        mutation below here uses, and the configuration.

    Notes
    -----
    The order is the content: read, judge, refresh, judge again, act.
    Both judgements sit here rather than inside the read, because each
    can report a decision and withdraw an armed request, and a helper
    that acquires a snapshot must not also be the place those happen.
    """
    pr = _read_snapshot(context, run=run)
    if _run_is_over(pr, run=run):
        return
    pr = _refresh_merge_state(pr, run=run)
    # The refresh refetched the pull request, so it also refetched who
    # wrote the commits. A push landing inside the retry window is
    # visible in this snapshot and nowhere else, and arming auto-merge on
    # the strength of the earlier one would merge it unreviewed.
    if _run_is_over(pr, run=run):
        return
    _act_on_merge_state(pr, run=run)


@dataclasses.dataclass(frozen=True, slots=True)
class AutomergeOptions:
    """CLI options for automerge execution.

    Attributes
    ----------
    merge_method : str
        Merge method to use: ``squash``, ``merge``, or ``rebase``.
    required_label : str or None
        Label that must be present on the PR, or None to skip label checks.
    dry_run : bool
        If True, logs the decision without calling the GitHub API.
    pull_request_number : int or None
        Explicit PR number override, or None to resolve from event payload.
    repository : str or None
        Repository override in ``owner/repo`` form, or None to resolve from
        event payload or environment.
    """

    merge_method: typ.Annotated[
        str,
        Parameter(
            help="Merge method to use (squash, merge, rebase).",
            env_var="INPUT_MERGE_METHOD",
        ),
    ] = "squash"
    required_label: typ.Annotated[
        str | None,
        Parameter(
            help="Required label on the pull request.",
            env_var="INPUT_REQUIRED_LABEL",
        ),
    ] = "dependencies"
    dry_run: typ.Annotated[
        bool,
        Parameter(
            help="Emit decision output without API calls.",
            env_var="INPUT_DRY_RUN",
        ),
    ] = False
    pull_request_number: typ.Annotated[
        int | None,
        Parameter(
            help="Pull request number override.",
            env_var="INPUT_PULL_REQUEST_NUMBER",
        ),
    ] = None
    repository: typ.Annotated[
        str | None,
        Parameter(
            help="Repository override in owner/repo form.",
            env_var="INPUT_REPOSITORY",
        ),
    ] = None


DEFAULT_AUTOMERGE_OPTIONS = AutomergeOptions()


@app.default
def main(
    *,
    github_token: typ.Annotated[
        str, Parameter(required=True, env_var="INPUT_GITHUB_TOKEN")
    ],
    options: AutomergeOptions = DEFAULT_AUTOMERGE_OPTIONS,
) -> None:
    """Evaluate a PR and enable auto-merge when policy allows.

    This is the CLI entrypoint. It loads configuration from environment
    variables and CLI arguments, evaluates the pull request against
    eligibility rules, and enables auto-merge if all criteria are met.

    Parameters
    ----------
    github_token : str
        GitHub token with ``contents:write`` and ``pull-requests:write``
        permissions. Read from ``INPUT_GITHUB_TOKEN`` environment variable.
    options : AutomergeOptions
        Configuration options including merge method, required label,
        dry-run mode, and optional repository/PR number overrides.

    Raises
    ------
    SystemExit
        Exits with code 1 on validation errors, missing configuration,
        or GitHub API failures. Error details are logged to stderr.
    """
    normalized_label = _normalize_label(options.required_label)
    normalized_merge_method = _normalize_merge_method(options.merge_method)
    config = AutomergeConfig(
        merge_method=normalized_merge_method,
        required_label=normalized_label,
        dry_run=options.dry_run,
    )

    event = _load_event()
    repo_full_name = _resolve_repository(options.repository, event)

    if options.dry_run:
        _handle_dry_run(
            event,
            repo_full_name,
            config=config,
        )
        return
    context = RuntimeContext(
        repo_full_name=repo_full_name,
        event=event,
        pull_request_number=options.pull_request_number,
    )
    _handle_live_execution(
        context,
        run=LiveRun(token=github_token, query=request_graphql, config=config),
    )


if __name__ == "__main__":
    app()
