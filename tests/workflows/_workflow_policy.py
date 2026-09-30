"""Runner-placement and ceiling policy for this repository's workflows.

The labels this repository may name, the exemptions that record a
deliberate exception to a placement rule with its reason, and the jobs
that occupy no runner. Policy only: nothing here reads a file, so a
change of policy never touches the reading boundary in
`_workflow_reading.py`, which re-exports these names for the contracts
that read both. Not a test module, so pytest does not collect it.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The Ubicloud shape every migrated Linux lane starts on. The recipe
#: allows a larger shape only on measured disk or wall-time evidence, and
#: this repository has produced none, so the larger labels are absent
#: from the recognized set below and a move to one would fail here.
UBICLOUD_LINUX: typ.Final[str] = "ubicloud-standard-2"

#: The GitHub-hosted Linux label. Present only where an exemption below
#: says why.
HOSTED_LINUX: typ.Final[str] = "ubuntu-latest"

#: Every runner label this repository is allowed to name, as exact
#: tokens. An unrecognized label fails rather than being classified by
#: the shape of its name, because "starts with ubicloud" would accept a
#: shape nobody measured and "contains ubuntu" would accept
#: `ubicloud-standard-2-ubuntu-2404` as a GitHub-hosted runner.
HOSTED_ARM_LINUX: typ.Final[str] = "ubuntu-24.04-arm"
RECOGNIZED_LINUX_LABELS: typ.Final[frozenset[str]] = frozenset(
    {UBICLOUD_LINUX, HOSTED_LINUX}
)
#: `windows-11-arm` and `ubuntu-24.04-arm` are the GitHub-hosted Arm runners.
#: The Linux Arm one is listed here, not with the Linux labels, because the
#: Ubicloud placement rule is about x86_64 work and no Ubicloud Arm shape has
#: been measured. Listing it here would let any Linux job take it unseen, so
#: `HOSTED_ARM_LINUX_JOBS` names the jobs that may, and a contract enforces it.
RECOGNIZED_OTHER_LABELS: typ.Final[frozenset[str]] = frozenset(
    {"macos-15", "windows-latest", "windows-11-arm", HOSTED_ARM_LINUX}
)

_MUTATION_REASON: typ.Final[str] = (
    "Mutation lanes stay GitHub-hosted: they are scheduled, they never "
    "block a developer, and public-repository minutes are free there."
)

#: Linux jobs that must stay on a GitHub-hosted runner, each with the
#: reason. A job absent from this mapping must run on Ubicloud.
HOSTED_LINUX_EXEMPTIONS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    (
        "test-export-ubicloud-cache-credentials.yml",
        "refuses-a-github-hosted-runner",
    ): (
        "The job exists to prove the action fails closed against a real "
        "GitHub-hosted cache endpoint. On Ubicloud the endpoint is the one "
        "the action accepts, so the job would pass without testing anything."
    ),
    ("test-setup-rust-sccache.yml", "exports-the-wrapper"): (
        "The job proves setup-rust's GitHub-hosted arm: a local sccache "
        "directory the action caches. On Ubicloud the action selects the "
        "proxy instead, so the job would test the other arm."
    ),
    ("test-setup-rust-sccache.yml", "refuses_a_missing_ubicloud_proxy"): (
        "The job proves expect-cache: ubicloud fails closed where there is "
        "no proxy. On Ubicloud the proxy is present and the job would pass "
        "without testing anything."
    ),
    ("mutation-cargo.yml", "detect"): _MUTATION_REASON,
    ("mutation-cargo.yml", "mutants"): _MUTATION_REASON,
    ("mutation-cargo.yml", "summarize"): _MUTATION_REASON,
    ("mutation-mutmut.yml", "mutants"): _MUTATION_REASON,
    ("dependabot-automerge.yml", "automerge"): (
        "A delayed-comment lane that waits on other checks rather than "
        "computing anything, and never blocks a developer."
    ),
}

#: The only jobs allowed to run on the GitHub-hosted Linux Arm runner, each
#: with the reason. The label sits outside the Linux placement rule, so a job
#: not named here could take it and bypass both the Ubicloud and the
#: fork-fallback contracts.
HOSTED_ARM_LINUX_JOBS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("test-install-makeutil.yml", "install-makeutil"): (
        "The aarch64 musl binary and its digest are proved only on real Arm "
        "hardware, and this repository has measured no Ubicloud Arm shape. "
        "The job's x86_64 leg follows the ordinary Ubicloud placement."
    ),
    ("test-install-makeutil.yml", "install-makeutil-cache-restore"): (
        "It restores the entry the aarch64 leg of `install-makeutil` saved, "
        "so it must run on the same architecture. The x86_64 leg follows the "
        "ordinary Ubicloud placement."
    ),
    ("test-setup-rust-mold.yml", "installs-on-linux"): (
        "The aarch64 mold archive is proved only on real Arm hardware, and "
        "this repository has measured no Ubicloud Arm shape. The job's x86_64 "
        "leg follows the ordinary Ubicloud placement."
    ),
}

#: Jobs that must run on Linux on every arm, each with the reason. The
#: placement rule skips a job with no Linux arm, which is right for a
#: macOS or Windows lane and wrong for one of these: moved to
#: `macos-15`, it would stop being checked at all while the only run of
#: its work left Linux.
LINUX_ONLY_JOBS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("ci.yml", "coverage"): (
        "The only lane that measures coverage, and the one the CodeScene "
        "upload and the ratchet read. The baseline is keyed by runner.os, so "
        "moving it off Linux would compare against a baseline nothing writes."
    ),
}

#: Lanes that answer the fork problem by skipping rather than falling
#: back, with the reason and the guard that has to be there.
#:
#: Falling back is the default because a skip leaves an external
#: contribution with no Linux CI. It is the wrong answer only where the
#: hosted runner cannot prove what the job exists to prove, in which
#: case a fallback would make the job pass while testing nothing.
FORK_FALLBACK_EXEMPTIONS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("test-ubicloud-sccache-proxy.yml", "reaches-the-proxy"): (
        "The job exists to prove sccache reaches Ubicloud's cache proxy, "
        "which is observable on an Ubicloud runner and nowhere else. A "
        "fallback to a GitHub-hosted runner would leave it green and "
        "proving nothing, so it skips a fork's pull request instead."
    ),
    ("test-ubicloud-sccache-proxy.yml", "selects-the-proxy-by-itself"): (
        "The job proves setup-rust selects Ubicloud's proxy on its own, "
        "which is observable on an Ubicloud runner and nowhere else, so "
        "it skips a fork's pull request rather than falling back."
    ),
    ("test-upload-codescene-coverage.yml", "cold-runner-contract"): (
        "The job installs the pinned CLI through the repository's own "
        "action tree, which a fork's pull request cannot reach in the shape "
        "the proof needs, so the job's own guard skips a fork's pull "
        "request rather than falling back."
    ),
}

#: The head-repository comparison an exempt lane must guard itself with.
#: Keyed on the head repository rather than on `github.repository`,
#: which is the base repository and matches a fork's pull request too.
FORK_SKIP_GUARD: typ.Final[str] = (
    "github.event.pull_request.head.repo.full_name == github.repository"
)

#: The one disjunct that may sit beside the guard. A workflow serving a
#: dispatch as well as a pull request has to let the dispatch through,
#: and a dispatch runs on the base repository, so there is no fork to
#: keep out on that arm. Written out exactly rather than matched loosely,
#: because this is the single escape the rule allows and a near miss
#: should fail rather than be accepted as close enough.
FORK_GUARD_EVENT_ESCAPE: typ.Final[str] = "github.event_name != 'pull_request'"

#: The other way the same arm is written: naming the one event the lane
#: also serves rather than excluding pull requests. A fork reaches this
#: repository through a pull request and through nothing else, so an arm
#: that requires a dispatch admits no fork either. Both spellings are
#: written out in full, and an arm naming any other event is refused,
#: because `github.event_name == 'pull_request'` has the same shape and
#: the opposite meaning.
FORK_GUARD_DISPATCH_ESCAPE: typ.Final[str] = "github.event_name == 'workflow_dispatch'"

#: The complete set of arms that keep a fork out without the comparison.
FORK_GUARD_ESCAPES: typ.Final[frozenset[str]] = frozenset(
    {FORK_GUARD_EVENT_ESCAPE, FORK_GUARD_DISPATCH_ESCAPE}
)

#: Jobs that only call another workflow. They occupy no runner and can
#: carry neither a label nor a ceiling.
CALLER_JOBS: typ.Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("dependabot-automerge-caller.yml", "automerge"),
        ("mutation-testing-caller.yml", "mutation"),
        ("test-dependabot-automerge.yml", "automerge"),
        ("test-mutation-cargo.yml", "mutation"),
        ("test-mutation-mutmut.yml", "mutation"),
    }
)
