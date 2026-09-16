"""Contract for where this repository's own jobs run, and for how long.

Two policies meet in every job header, and both are invisible once the
workflow is green.

The first is runner placement. A Linux lane that blocks a developer runs
on Ubicloud, because that is the image this repository's consumers run
its actions on, and because a self-test that only ever proves an action
on GitHub's image proves it somewhere nobody ships. The exceptions are
deliberate and each one is named below with the reason it is an
exception, so that removing a reason is a code change rather than a
silent drift back to the default label.

The second is the job ceiling. A job without `timeout-minutes` inherits
GitHub's six-hour default, which is not a budget anybody chose; it is
the absence of one. Every lane this repository owns carries a ceiling
from a named tier, and the tiers are sized from measured run history
rather than from a round number that looked safe. "Runner placement and
job ceilings" in `docs/developers-guide.md` records the measurements.

Both policies are asserted structurally rather than textually. A
`runs-on` is read as a value and compared to an exact label, never by
prefix or substring: `ubicloud-standard-8` must not satisfy a rule about
`ubicloud-standard-2`, and `actions/cache-audit` must not satisfy a rule
about `actions/cache`. A job is read as a whole, so a job carrying both
`uses` and `runs-on` fails rather than being judged on whichever field
is looked at first.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"

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
RECOGNIZED_LINUX_LABELS: typ.Final[frozenset[str]] = frozenset(
    {UBICLOUD_LINUX, HOSTED_LINUX}
)
RECOGNIZED_OTHER_LABELS: typ.Final[frozenset[str]] = frozenset(
    {"macos-15", "windows-latest", "windows-11-arm"}
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
    ("mutation-cargo.yml", "detect"): _MUTATION_REASON,
    ("mutation-cargo.yml", "mutants"): _MUTATION_REASON,
    ("mutation-cargo.yml", "summarize"): _MUTATION_REASON,
    ("mutation-mutmut.yml", "mutants"): _MUTATION_REASON,
    ("dependabot-automerge.yml", "automerge"): (
        "A delayed-comment lane that waits on other checks rather than "
        "computing anything, and never blocks a developer."
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


def _strip_expression_wrapper(condition: str) -> str:
    """Return *condition* without its `${{ }}` wrapper and extra spacing.

    A job condition may carry the wrapper or omit it, and the two mean
    the same thing to GitHub.
    """
    text = " ".join(condition.split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text


def _disjunct_skips_forks(disjunct: str) -> bool:
    """Return True when one arm of a condition cannot admit a fork.

    Either it requires the head-repository comparison, or it is the
    dispatch escape, which no fork reaches.
    """
    operands = [operand.strip(" ()") for operand in disjunct.split("&&")]
    operands = [operand for operand in operands if operand]
    if FORK_SKIP_GUARD in operands:
        return True
    return operands == [FORK_GUARD_EVENT_ESCAPE]


def _skips_forks(condition: str) -> bool:
    """Return True when *condition* genuinely keeps a fork's run away.

    Containing the comparison is not enough, which is what the substring
    check that preceded this helper tested. `true || <guard>` holds the
    comparison and runs on every fork, because the arm beside it is
    always taken.

    So every arm of the condition has to be unreachable by a fork, not
    just one of them. An arm qualifies by requiring the comparison, or
    by being the dispatch escape. A condition with no arms, which is an
    absent `if`, qualifies as nothing: an unguarded job is the default
    this rule exists to refuse.
    """
    text = _strip_expression_wrapper(condition)
    disjuncts = [disjunct for disjunct in text.split("||") if disjunct.strip()]
    if not disjuncts:
        return False
    return all(_disjunct_skips_forks(disjunct) for disjunct in disjuncts)


#: Job ceilings by tier, in minutes, with the measurement each was sized
#: against recorded in the developers' guide.
TIMEOUT_TIERS: typ.Final[cabc.Mapping[str, int]] = {
    # Checkout, run an action, assert its outputs. No download of a
    # release archive, no compilation. Longest observed: 13 s.
    "assertion": 10,
    # Downloads and verifies a pinned release archive. Longest observed:
    # 5 min 40 s, on the Windows Whitaker leg.
    "install": 15,
    # Cross-compiles the toy application. Longest observed: 4 min 36 s,
    # on a Windows leg.
    "build": 20,
    # Runs the Python suite uninstrumented. Longest observed: 12 min 45 s.
    "suite": 20,
    # Runs the same suite under the coverage action. Longest observed:
    # 6 min 53 s.
    "coverage": 30,
}

#: Every job this repository owns, mapped to its tier. Exhaustive by
#: construction: `test_every_job_is_classified` fails when a job appears
#: that is in neither this mapping nor `CONSUMER_OWNED_JOBS` nor
#: `CALLER_JOBS`.
JOB_TIERS: typ.Final[cabc.Mapping[tuple[str, str], str]] = {
    ("ci.yml", "python-tests"): "suite",
    ("ci.yml", "python-tests-windows"): "suite",
    ("ci.yml", "coverage"): "coverage",
    ("coverage-main.yml", "coverage-upload"): "coverage",
    ("rust-toy-app.yml", "build-release"): "build",
    ("test-determine-release-modes.yml", "test-determine-modes"): "assertion",
    ("test-export-cargo-metadata.yml", "test-export-metadata"): "assertion",
    (
        "test-export-cargo-metadata.yml",
        "test-export-metadata-env-overrides",
    ): "assertion",
    (
        "test-export-ubicloud-cache-credentials.yml",
        "refuses-a-github-hosted-runner",
    ): "assertion",
    ("test-install-mdtablefix.yml", "install-mdtablefix"): "install",
    ("test-install-mdtablefix.yml", "install-mdtablefix-no-prebuilt"): "install",
    ("test-install-tool.yml", "installs-and-caches"): "install",
    ("test-install-tool.yml", "refuses-an-unpinned-version"): "assertion",
    ("test-install-tool.yml", "refuses-an-unsupported-target"): "assertion",
    ("test-install-whitaker.yml", "install-whitaker"): "install",
    ("test-install-whitaker.yml", "install-whitaker-windows"): "install",
    ("test-install-whitaker.yml", "install-whitaker-failure"): "assertion",
    ("test-resolve-workflow-source.yml", "resolve"): "assertion",
    (
        "test-rust-build-release-root-discovery.yml",
        "test-action-setup-root",
    ): "assertion",
    ("test-rustflags-export.yml", "rust-build-release-exports"): "assertion",
    (
        "test-rustflags-export.yml",
        "rust-build-release-defers-to-inherited",
    ): "assertion",
    ("test-rustflags-export.yml", "setup-rust-exports"): "assertion",
    ("test-rustflags-export.yml", "setup-rust-with-inherited"): "assertion",
    ("test-rustflags-export.yml", "setup-rust-toolchain-available"): "assertion",
    ("test-setup-rust-sccache.yml", "exports-the-wrapper"): "assertion",
    ("test-setup-rust-sccache.yml", "respects_a_caller_backend_choice"): "assertion",
    ("test-setup-rust-sccache.yml", "respects_a_caller_wrapper"): "assertion",
    (
        "test-setup-rust-sccache.yml",
        "restores_a_caller_cache_service_choice",
    ): "assertion",
    ("test-stage-release-artefacts.yml", "test-stage-artefacts"): "assertion",
    (
        "test-stage-release-artefacts.yml",
        "test-stage-artefacts-env-overrides",
    ): "assertion",
    (
        "test-stage-release-artefacts.yml",
        "test-stage-artefacts-powershell-help",
    ): "assertion",
    ("test-stage-release-artefacts.yml", "test-stage-artefacts-binstall"): "assertion",
    ("test-ubicloud-sccache-proxy.yml", "reaches-the-proxy"): "assertion",
    ("test-upload-release-assets.yml", "test-upload-assets-dry-run"): "assertion",
    ("test-upload-release-assets.yml", "test-upload-assets-env-overrides"): "assertion",
}

#: Jobs in reusable workflows that other repositories call. Their run
#: history lives in those repositories, so this repository cannot size a
#: ceiling for them from evidence and does not pretend to. They are
#: outside the ceiling contract; their placement is still governed by
#: `HOSTED_LINUX_EXEMPTIONS`.
CONSUMER_OWNED_JOBS: typ.Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("mutation-cargo.yml", "detect"),
        ("mutation-cargo.yml", "mutants"),
        ("mutation-cargo.yml", "summarize"),
        ("mutation-mutmut.yml", "mutants"),
        ("dependabot-automerge.yml", "automerge"),
    }
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

#: A `runs-on` that defers to the job's matrix, captured exactly. A
#: looser pattern would read `${{ matrix.os }}-latest` as a bare matrix
#: reference and lose the suffix.
_MATRIX_REFERENCE: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}$"
)

#: The sanctioned fork fallback, parsed rather than string-compared so
#: that each part can be asserted on its own.
#:
#: A fork's pull request cannot obtain an Ubicloud runner and would
#: queue until the job ceiling, so a lane reachable by `pull_request`
#: selects its label from the head repository. Skipping instead would
#: leave an external contribution with no Linux CI at all.
#:
#: The field path is matched exactly. Swapping `fork` for a sibling such
#: as `private` changes which pull requests fall back and matches
#: nothing here, which is the point: the rule is about forks, not about
#: whichever boolean sits next to it.
_FORK_AWARE_RUNS_ON: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*github\.event\.pull_request\.head\.repo\.fork"
    r"\s*&&\s*'(?P<fork_arm>[^']+)'"
    r"\s*\|\|\s*'(?P<base_arm>[^']+)'\s*\}\}$"
)


def _triggers(name: str) -> set[str]:
    """Return the event names the workflow *name* is triggered by."""
    document = _load_workflow(name)
    # PyYAML reads the bare key `on` as the boolean True.
    on = document.get(True, document.get("on"))
    if isinstance(on, dict):
        return set(on)
    if isinstance(on, list):
        return set(on)
    return {on} if isinstance(on, str) else set()


def _fork_arms(runs_on: str) -> tuple[str, str] | None:
    """Return the (fork, non-fork) labels of a fork-aware `runs-on`."""
    match = _FORK_AWARE_RUNS_ON.match(" ".join(runs_on.split()))
    if match is None:
        return None
    return match.group("fork_arm"), match.group("base_arm")


def _load_workflow(name: str) -> dict[str, typ.Any]:
    """Return the parsed workflow named *name*."""
    return yaml.safe_load((WORKFLOWS_DIRECTORY / name).read_text(encoding="utf-8"))


#: Both extensions GitHub accepts for a workflow file. Scanning only
#: `.yml` would let a `.yaml` workflow past every rule in this module
#: while the module still claimed to be exhaustive.
WORKFLOW_SUFFIXES: typ.Final[tuple[str, ...]] = (".yml", ".yaml")


def _workflow_names() -> list[str]:
    """Return every workflow file name, sorted."""
    return sorted(
        path.name
        for path in WORKFLOWS_DIRECTORY.iterdir()
        if path.suffix in WORKFLOW_SUFFIXES and path.is_file()
    )


def _jobs(name: str) -> cabc.Iterator[tuple[str, dict[str, typ.Any]]]:
    """Yield each job id and body in the workflow named *name*."""
    yield from (_load_workflow(name).get("jobs") or {}).items()


def _matrix_values(job: cabc.Mapping[str, typ.Any], key: str) -> set[str]:
    """Return every value the matrix assigns to *key* in *job*."""
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    values: set[str] = set()
    direct = matrix.get(key)
    if isinstance(direct, list):
        values.update(str(value) for value in direct)
    for entry in matrix.get("include") or []:
        if key in entry:
            values.add(str(entry[key]))
    return values


def _runner_labels(job: cabc.Mapping[str, typ.Any]) -> set[str]:
    """Return every runner label *job* can run on.

    A `runs-on` that defers to the matrix expands to the values the
    matrix supplies for that key, so a matrix job is judged on each arm
    rather than on the expression.
    """
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str):
        return set()
    if match := _MATRIX_REFERENCE.match(runs_on):
        values = _matrix_values(job, match.group(1))
        return {label for value in values for label in _expand(value)}
    return _expand(runs_on)


def _expand(value: str) -> set[str]:
    """Return every label a single `runs-on` value can resolve to."""
    if arms := _fork_arms(value):
        return set(arms)
    return {value}


def _runs_on_values(job: cabc.Mapping[str, typ.Any]) -> set[str]:
    """Return *job*'s `runs-on` values before any fork arm is expanded.

    The placement rule is about the shape a lane declares, not only the
    labels it can reach, so it has to see the expression rather than its
    arms.
    """
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str):
        return set()
    if match := _MATRIX_REFERENCE.match(runs_on):
        return _matrix_values(job, match.group(1))
    return {runs_on}


def _is_unparsed_expression(value: str) -> bool:
    """Return True for a `runs-on` expression this module cannot read.

    An expression that is not the sanctioned fork selector could resolve
    to anything, including a paid label on a fork. Treating it as "not a
    Linux lane" and skipping is how the rule gets defeated by a change
    that merely renames the field it keys on, so it is treated as a
    Linux lane and fails.
    """
    return value.strip().startswith("${{") and _fork_arms(value) is None


def _reaches_linux(value: str) -> bool:
    """Return True when a `runs-on` value can land on a Linux runner."""
    if _is_unparsed_expression(value):
        return True
    return bool(_expand(value) & RECOGNIZED_LINUX_LABELS)


def _all_jobs() -> list[tuple[str, str]]:
    """Return every (workflow, job id) pair in the repository, sorted."""
    return sorted(
        (name, job_id) for name in _workflow_names() for job_id, _ in _jobs(name)
    )


def _runner_job_ids() -> list[tuple[str, str]]:
    """Return every pair for a job that occupies a runner."""
    return [pair for pair in _all_jobs() if pair not in CALLER_JOBS]


def _label_token(label: str) -> re.Pattern[str]:
    r"""Return a pattern matching *label* as a whole token.

    The delimiters are explicit rather than `\b`, because a word
    boundary sits inside a hyphenated label and would find
    `ubuntu-latest` in a longer name that merely contains it.
    """
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(label)}(?![A-Za-z0-9_-])")


def _identifier(*parts: str) -> str:
    """Return a readable pytest id for *parts*."""
    return "::".join(parts)


@pytest.mark.parametrize(
    ("workflow", "job_id"),
    _all_jobs(),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_every_job_is_classified(workflow: str, job_id: str) -> None:
    """Every job belongs to exactly one of the three partitions.

    A new workflow added without a decision about its runner and its
    ceiling fails here rather than inheriting both defaults quietly.
    """
    pair = (workflow, job_id)
    memberships = [
        pair in JOB_TIERS,
        pair in CONSUMER_OWNED_JOBS,
        pair in CALLER_JOBS,
    ]
    assert sum(memberships) == 1, (
        f"{_identifier(workflow, job_id)} must appear in exactly one of "
        "JOB_TIERS, CONSUMER_OWNED_JOBS or CALLER_JOBS; it appears in "
        f"{sum(memberships)}"
    )


@pytest.mark.parametrize(("workflow", "job_id"), _all_jobs())
def test_a_job_either_calls_a_workflow_or_names_a_runner(
    workflow: str, job_id: str
) -> None:
    """A job declares `uses` or `runs-on`, never both and never neither.

    Reading the two fields independently would accept a job carrying
    both, where the runner label is decoration and the reusable workflow
    decides where the work lands.
    """
    job = dict(_jobs(workflow))[job_id]
    has_uses = "uses" in job
    has_runs_on = "runs-on" in job
    assert has_uses != has_runs_on, (
        f"{_identifier(workflow, job_id)} declares "
        f"uses={has_uses} and runs-on={has_runs_on}; exactly one is required"
    )


@pytest.mark.parametrize(("workflow", "job_id"), _runner_job_ids())
def test_every_runner_label_is_recognized(workflow: str, job_id: str) -> None:
    """Each arm names a label this repository has decided about.

    The recognized sets hold exact tokens, so a shape nobody measured,
    such as `ubicloud-standard-8`, fails here instead of being read as
    "an Ubicloud runner".
    """
    job = dict(_jobs(workflow))[job_id]
    labels = _runner_labels(job)
    assert labels, f"{_identifier(workflow, job_id)} resolves to no runner label"
    recognized = RECOGNIZED_LINUX_LABELS | RECOGNIZED_OTHER_LABELS
    unrecognized = labels - recognized
    assert not unrecognized, (
        f"{_identifier(workflow, job_id)} names unrecognized runner labels "
        f"{sorted(unrecognized)}; add the label to the recognized sets with a "
        "reason, or use one already there"
    )


@pytest.mark.parametrize(("workflow", "job_id"), _runner_job_ids())
def test_a_linux_lane_declares_the_placement_its_triggers_require(
    workflow: str, job_id: str
) -> None:
    """Linux work runs on Ubicloud, with a fork fallback where forks reach it.

    Three shapes, and which one a lane must use is decided by its
    triggers rather than by preference. A lane an exemption names stays
    GitHub-hosted. A lane a fork's pull request can reach selects its
    label from the head repository, because a fork cannot obtain an
    Ubicloud runner and would otherwise queue until the ceiling.
    Everything else names Ubicloud outright.
    """
    job = dict(_jobs(workflow))[job_id]
    values = {value for value in _runs_on_values(job) if _reaches_linux(value)}
    if not values:
        pytest.skip("no Linux arm")
    if (workflow, job_id) in HOSTED_LINUX_EXEMPTIONS:
        assert values == {HOSTED_LINUX}, (
            f"{_identifier(workflow, job_id)} is exempt from the Ubicloud "
            f"rule but declares {sorted(values)}; remove the exemption or "
            f"restore {HOSTED_LINUX!r}"
        )
        return
    if (
        "pull_request" in _triggers(workflow)
        and (workflow, job_id) not in FORK_FALLBACK_EXEMPTIONS
    ):
        for value in values:
            arms = _fork_arms(value)
            assert arms is not None, (
                f"{_identifier(workflow, job_id)} can be reached by a fork's "
                f"pull request but declares {value!r}; key the label on "
                "github.event.pull_request.head.repo.fork so that a fork "
                "falls back rather than queueing for a runner it cannot have"
            )
            assert arms == (HOSTED_LINUX, UBICLOUD_LINUX), (
                f"{_identifier(workflow, job_id)} falls back to {arms[0]!r} "
                f"and otherwise runs on {arms[1]!r}; the fork arm must be "
                f"{HOSTED_LINUX!r} and the other {UBICLOUD_LINUX!r}, so that "
                "a fork never lands on a paid runner"
            )
        return
    assert values == {UBICLOUD_LINUX}, (
        f"{_identifier(workflow, job_id)} neither falls back for forks nor "
        f"needs to, so it must name {UBICLOUD_LINUX!r} outright rather than "
        f"{sorted(values)}"
    )


@pytest.mark.parametrize(
    ("workflow", "job_id"), sorted(FORK_FALLBACK_EXEMPTIONS), ids=_identifier
)
def test_a_fork_skipping_lane_really_skips_forks(workflow: str, job_id: str) -> None:
    """A lane excused the fallback carries the guard that replaces it.

    Without this the exemption is a note, and deleting the `if` leaves a
    fork's pull request queueing for a runner it cannot have, which is
    the outcome the whole rule exists to prevent.
    """
    job = dict(_jobs(workflow))[job_id]
    condition = " ".join(str(job.get("if", "")).split())
    assert _skips_forks(condition), (
        f"{_identifier(workflow, job_id)} is excused the fork fallback "
        "because it skips forks, but its condition does not effectively "
        "compare the head repository. Every arm of the condition must "
        "either require the comparison or be the dispatch escape "
        f"{FORK_GUARD_EVENT_ESCAPE!r}, because an unguarded arm beside the "
        f"comparison lets a fork through: {condition!r}"
    )


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        pytest.param(FORK_SKIP_GUARD, True, id="bare"),
        pytest.param("${{ " + FORK_SKIP_GUARD + " }}", True, id="wrapped"),
        pytest.param(
            f"github.event_name == 'push' && {FORK_SKIP_GUARD}",
            True,
            id="conjunction",
        ),
        pytest.param(
            f"{FORK_GUARD_EVENT_ESCAPE} || {FORK_SKIP_GUARD}",
            True,
            id="dispatch-escape-beside-the-guard",
        ),
        pytest.param(f"true || {FORK_SKIP_GUARD}", False, id="always-true-disjunction"),
        pytest.param(
            f"{FORK_SKIP_GUARD} || github.event_name == 'push'",
            False,
            id="unguarded-arm-beside-the-guard",
        ),
        pytest.param(
            f"{FORK_GUARD_EVENT_ESCAPE} || true",
            False,
            id="escape-beside-an-unguarded-arm",
        ),
        pytest.param("github.event_name == 'push'", False, id="no-guard"),
        pytest.param("", False, id="empty"),
    ],
)
def test_the_fork_guard_is_judged_by_effect_not_by_substring(
    condition: str,
    expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
) -> None:
    """A guard inside a disjunction does not by itself skip forks.

    `true || <guard>` contains the comparison and runs on every fork, so
    a substring check calls it guarded and it is not. That is the
    mutation that defeated the previous assertion. The narrow direction
    matters just as much: this repository's own exempt lane writes
    `<dispatch escape> || <guard>`, which is correct, and a rule that
    refused every disjunction would reject it.
    """
    assert _skips_forks(condition) is expected


@pytest.mark.parametrize(
    ("workflow", "job_id"), sorted(HOSTED_LINUX_EXEMPTIONS), ids=_identifier
)
def test_every_exemption_names_a_live_hosted_job(workflow: str, job_id: str) -> None:
    """An exemption whose job has gone must go with it.

    Without this the mapping accumulates permissions for jobs that no
    longer exist, and the next job to take one of those names inherits a
    decision nobody made about it.
    """
    jobs = dict(_jobs(workflow))
    assert job_id in jobs, (
        f"{_identifier(workflow, job_id)} is exempt from the Ubicloud rule "
        "but no such job exists; delete the exemption"
    )
    assert HOSTED_LINUX in _runner_labels(jobs[job_id]), (
        f"{_identifier(workflow, job_id)} is exempt from the Ubicloud rule "
        f"but no arm runs on {HOSTED_LINUX!r}; delete the exemption"
    )


@pytest.mark.parametrize(("workflow", "job_id"), sorted(JOB_TIERS), ids=_identifier)
def test_a_job_carries_the_ceiling_of_its_tier(workflow: str, job_id: str) -> None:
    """Each owned job declares the `timeout-minutes` its tier specifies.

    Asserting the exact value rather than "some ceiling is present"
    keeps the guide's table and the workflows from drifting apart, and
    makes raising a ceiling a decision with a measurement behind it.
    """
    jobs = dict(_jobs(workflow))
    assert job_id in jobs, f"{_identifier(workflow, job_id)} no longer exists"
    tier = JOB_TIERS[(workflow, job_id)]
    expected = TIMEOUT_TIERS[tier]
    declared = jobs[job_id].get("timeout-minutes")
    assert declared == expected, (
        f"{_identifier(workflow, job_id)} is in the {tier!r} tier and must "
        f"declare timeout-minutes: {expected}; it declares {declared!r}"
    )


@pytest.mark.parametrize(("workflow", "job_id"), _runner_job_ids())
def test_no_step_condition_names_a_runner_label(workflow: str, job_id: str) -> None:
    """A step selects a platform by `runner.os`, never by a runner label.

    Before this rule, fourteen conditions in `ci.yml` read
    `matrix.os == 'ubuntu-latest'`, so changing the label silently
    switched off every lint, spelling and diagram check on the only leg
    that ran them. The job would have stayed green while doing almost
    nothing. `runner.os` says what the condition means.
    """
    job = dict(_jobs(workflow))[job_id]
    offenders = [
        (step.get("name", "<unnamed>"), condition)
        for step in job.get("steps") or []
        if isinstance(condition := str(step.get("if", "")), str)
        for label in RECOGNIZED_LINUX_LABELS | RECOGNIZED_OTHER_LABELS
        if _label_token(label).search(condition)
    ]
    assert not offenders, (
        f"{_identifier(workflow, job_id)} has step conditions naming a runner "
        f"label: {offenders}; compare `runner.os` instead"
    )


@pytest.mark.parametrize(
    ("workflow", "job_id"), sorted(CALLER_JOBS | CONSUMER_OWNED_JOBS), ids=_identifier
)
def test_an_unowned_job_still_exists(workflow: str, job_id: str) -> None:
    """A job excused from the ceiling contract must still be real.

    The two exclusion sets are the only way out of the ceiling rule, so
    a stale entry in either is a hole in it.
    """
    assert job_id in dict(_jobs(workflow)), (
        f"{_identifier(workflow, job_id)} is excused from the ceiling "
        "contract but no such job exists; delete the entry"
    )
