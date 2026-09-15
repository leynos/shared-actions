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


def _load_workflow(name: str) -> dict[str, typ.Any]:
    """Return the parsed workflow named *name*."""
    return yaml.safe_load((WORKFLOWS_DIRECTORY / name).read_text(encoding="utf-8"))


def _workflow_names() -> list[str]:
    """Return every workflow file name, sorted."""
    return sorted(path.name for path in WORKFLOWS_DIRECTORY.glob("*.yml"))


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
        return _matrix_values(job, match.group(1))
    return {runs_on}


def _all_jobs() -> list[tuple[str, str]]:
    """Return every (workflow, job id) pair in the repository, sorted."""
    return sorted(
        (name, job_id) for name in _workflow_names() for job_id, _ in _jobs(name)
    )


def _runner_job_ids() -> list[tuple[str, str]]:
    """Return every pair for a job that occupies a runner."""
    return [pair for pair in _all_jobs() if pair not in CALLER_JOBS]


def _linux_arms() -> list[tuple[str, str, str]]:
    """Return every (workflow, job id, label) arm on a Linux label."""
    arms: list[tuple[str, str, str]] = []
    for name in _workflow_names():
        for job_id, job in _jobs(name):
            arms.extend(
                (name, job_id, label)
                for label in sorted(_runner_labels(job))
                if label in RECOGNIZED_LINUX_LABELS
            )
    return arms


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


@pytest.mark.parametrize(
    ("workflow", "job_id", "label"),
    _linux_arms(),
    ids=lambda value: value,
)
def test_a_linux_arm_runs_on_ubicloud_unless_exempt(
    workflow: str, job_id: str, label: str
) -> None:
    """Linux work runs on Ubicloud unless an exemption says otherwise.

    The exemption is keyed on the job, so moving the rule's subject to a
    new job id fails here rather than carrying the old permission along.
    """
    if (workflow, job_id) in HOSTED_LINUX_EXEMPTIONS:
        assert label == HOSTED_LINUX, (
            f"{_identifier(workflow, job_id)} is exempt from the Ubicloud "
            f"rule but runs on {label!r}; remove the exemption or restore "
            f"{HOSTED_LINUX!r}"
        )
        return
    assert label == UBICLOUD_LINUX, (
        f"{_identifier(workflow, job_id)} runs Linux work on {label!r}; "
        f"use {UBICLOUD_LINUX!r} or add an exemption naming the reason"
    )


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
