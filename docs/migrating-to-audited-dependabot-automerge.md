# Migrating to the audited Dependabot auto-merge

This guide covers the next major tag of the Dependabot auto-merge reusable
workflow. Eligibility now depends on who wrote each commit on the branch, not
only on who opened the pull request. Read this before pulling in that tag, and
before pushing another fix onto a Dependabot branch.

## What changed, and why

Auto-merge skips human review, so eligibility has to answer more than "did
Dependabot open this?". Opening a pull request is not the same as writing what
is in it. Once Dependabot has opened one, anything anyone pushes to that branch
would previously have merged under the same rule, unreviewed. That is not
theoretical: a workflow change reached a trunk this way on 2026-09-05, in a
bump whose title and author were Dependabot's throughout.

**Every commit on the branch must be Dependabot's, wherever the commit list can
be read.** What the workflow now guarantees is not that every merged branch was
audited, but that no branch merges over a foreign commit the audit saw.

## What this means for your repository

**A branch you have pushed to will no longer merge unattended.** It skips with
`automerge_reason=foreign-commit:<sha>`, and the run logs a notice naming each
such commit and its author. The remedy is to open that change as its own pull
request with its own review, not to remove the check.

**A co-authored commit counts as the co-author's.** A commit Dependabot pushed
but a person co-wrote carries that person's change, so it makes the branch
ineligible. A check that merely looked for Dependabot among the authors would
wave it through, which is why it does not.

**An armed request is withdrawn, not left in place.** GitHub keeps an
auto-merge request alive across a push, so a request armed while the branch was
still Dependabot's would merge the commit that made it foreign as soon as the
required checks passed. Those runs report `automerge_status=cancelled`.

**Maintaining an edited branch becomes manual work.** Dependabot stops rebasing
a branch once it carries commits Dependabot did not write, so keeping it
current against the base branch is yours from then on. `@dependabot recreate`
still works, but it rebuilds the branch from scratch and discards everything
pushed onto it, so it abandons the manual edits rather than preserving them.

The practical advice is to stop treating a Dependabot branch as somewhere to
put a fix. Where a bump needs a code change to land, make that change on its
own branch, get it reviewed, and let Dependabot rebase onto it.

## What to expect in the logs

Every run emits three new lines alongside the existing `automerge_*` outputs:

| Output | Values |
| --- | --- |
| `automerge_commit_audit` | `clean`, `foreign`, or `unreadable` |
| `automerge_commit_pages_read` | Pages of the commit connection fetched |
| `automerge_commits_audited` | Commits judged |

`automerge_commit_audit` is bounded and carries no commit identifier, so it can
be counted across repositories. The two counts distinguish a run that read one
page of a longer branch from one that read the branch.

`automerge_status` gains `cancelled`, for a run that withdrew an armed request
rather than merely declining to arm one.

## Where the check does not run

Where the commit list cannot be read at all, the check fails open: the run
proceeds on the pull request's author alone and logs a warning saying so. A
query fault that halted every consumer's auto-merge at once would be a worse
failure than the one this prevents, and the warning is what keeps that loss
visible rather than silent.

Two narrower cases fail the other way, because the commit is visible and only
its authorship is in doubt. A commit whose credited authors came back
truncated, and a commit crediting nobody at all, are both reported as foreign.
A partial or absent credit list certifies nothing, and the whole point of the
check is evidence.

## Nothing to change in your caller

No inputs are added, removed, or renamed, and no permissions change. The
workflow still needs `contents: write` and `pull-requests: write`, required
status checks on the default branch, and auto-merge enabled in the repository
settings. Pulling in the new tag is enough.

## Further reading

- [Dependabot auto-merge reusable workflow](./dependabot-automerge-workflow.md)
  for the merge-state rules and the full decision log.
- [Users' guide](./users-guide.md) for the eligibility summary.
- [Developer's guide](./developers-guide.md) for the commit audit's internals.
