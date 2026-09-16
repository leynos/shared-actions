"""Contract that each cache key in `ci.yml` is written by exactly one job.

A GitHub cache key has one owner. When two jobs in a workflow write the
same key, one reserves it and the other is refused, and the entry can end
up written and then not readable. Issue #483 caught that happening twice
over in this workflow: `merman-cli-Linux-X64-0.7.0` saved on one run and
missed on the next, and `Linux-cargo-<hash>` was refused its reservation
on both runs because `python-tests` and `coverage` each called
`setup-rust`, and `setup-rust` wrote that key unconditionally.

Two rules, because the defect has two shapes. One says exactly one job
turns `setup-rust` into a writer, which covers the key the action owns
and which a caller cannot see in the workflow text. The other says no two
jobs write the same key directly, which covers every key the workflow
spells out itself, and which is the shape the Merman entry had.

"Exactly one" rather than "at most one" in both directions: a workflow
where nobody writes the key is not a fixed workflow, it is one with a
cache that never warms, and the failure would look like slowness rather
than like an error.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

#: The action whose cargo archive key is the one #483 found contended.
SETUP_RUST: typ.Final[str] = "./.github/actions/setup-rust"

#: The input that decides whether a `setup-rust` call writes that key.
SAVE_INPUT: typ.Final[str] = "save-cache"

#: `setup-rust` saves unless a caller says otherwise, so an absent input
#: means the same as "true". Asserting on the absent case matters: a job
#: added later without the input is a writer, and this contract has to
#: see it as one.
SAVES_BY_DEFAULT: typ.Final[bool] = True

#: The prefix identifying a step that writes a cache entry.
#: `actions/cache/restore` deliberately does not match: reading a key is
#: what every job may do, and only writing needs an owner.
CACHE_WRITER_PREFIX: typ.Final[str] = "actions/cache@"

# The parsed workflow, modelled rather than left as `Any`. `with` is a
# Python keyword, so the functional form is the only way to name it. Every
# field is optional because a step carries `uses` or `run` and not both,
# and because these tests read a handful of fields out of a document that
# has many more.
WorkflowStep = typ.TypedDict(
    "WorkflowStep",
    {"name": str, "uses": str, "run": str, "with": "dict[str, str]"},
    total=False,
)


class WorkflowJob(typ.TypedDict, total=False):
    """One job of a workflow, as much of it as these tests read."""

    steps: list[WorkflowStep]


class Workflow(typ.TypedDict, total=False):
    """A parsed workflow document."""

    jobs: dict[str, WorkflowJob]


def _as_workflow(document: object) -> Workflow:
    """Narrow a parsed YAML document to the shape these tests read.

    The check is at the boundary and deliberate. `yaml.safe_load` returns
    `Any`, so without it a workflow that had lost its `jobs` mapping
    would read as no jobs at all, and every rule below would pass by
    having nothing to judge.
    """
    if not isinstance(document, dict):
        msg = f"{CI_WORKFLOW} is not a mapping"
        raise TypeError(msg)
    if not isinstance(document.get("jobs"), dict):
        msg = f"{CI_WORKFLOW} has no jobs mapping"
        raise TypeError(msg)
    return typ.cast("Workflow", document)


@pytest.fixture(scope="module")
def ci_document() -> Workflow:
    """Return the parsed `ci.yml`, narrowed to the shape read here."""
    return _as_workflow(yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8")))


def _saves_cache(step: WorkflowStep) -> bool:
    """Return True when a `setup-rust` step writes the cargo archive key."""
    given = step.get("with", {}).get(SAVE_INPUT)
    if given is None:
        return SAVES_BY_DEFAULT
    return str(given).strip().lower() == "true"


def _cargo_archive_writers(document: Workflow) -> list[str]:
    """Return the names of jobs whose `setup-rust` call writes the key."""
    return [
        name
        for name, job in document.get("jobs", {}).items()
        for step in job.get("steps", [])
        if step.get("uses") == SETUP_RUST and _saves_cache(step)
    ]


def _direct_cache_keys(document: Workflow) -> dict[str, set[str]]:
    """Return the jobs writing each key named by an `actions/cache` step."""
    owners: dict[str, set[str]] = {}
    for name, job in document.get("jobs", {}).items():
        for step in job.get("steps", []):
            if not str(step.get("uses", "")).startswith(CACHE_WRITER_PREFIX):
                continue
            key = str(step.get("with", {}).get("key", "")).strip()
            if key:
                owners.setdefault(key, set()).add(name)
    return owners


class TestCacheKeyOwnership:
    """Who writes each cache key this workflow depends on."""

    def test_exactly_one_job_writes_the_cargo_archive_key(
        self, ci_document: Workflow
    ) -> None:
        """One `setup-rust` call owns the cargo archive cache, and one only.

        Two writers is the defect: the second is refused its reservation
        and the entry can be left written but unreadable, which reads in
        the log as an unrelated warning rather than as a broken cache.
        """
        writers = _cargo_archive_writers(ci_document)
        assert len(writers) == 1, (
            f"{len(writers)} jobs write the setup-rust cargo archive key "
            f"({sorted(writers)}); exactly one must, so pass "
            f"`{SAVE_INPUT}: 'false'` in every other job"
        )

    def test_no_key_is_written_by_two_jobs(self, ci_document: Workflow) -> None:
        """No key named in the workflow is written by more than one job.

        This is the shape the Merman entry had, spelled out in the
        workflow rather than hidden inside an action, and it is the
        general rule of which the cargo archive key is one instance.
        """
        contended = {
            key: sorted(owners)
            for key, owners in _direct_cache_keys(ci_document).items()
            if len(owners) > 1
        }
        assert not contended, (
            f"these cache keys are written by more than one job: {contended}; "
            "give each one owner and let the others use actions/cache/restore"
        )

    @pytest.mark.parametrize(
        ("step", "expected"),
        [
            pytest.param({"uses": SETUP_RUST}, True, id="no-with-block"),
            pytest.param({"uses": SETUP_RUST, "with": {}}, True, id="empty-with-block"),
            pytest.param(
                {"uses": SETUP_RUST, "with": {SAVE_INPUT: "true"}}, True, id="true"
            ),
            pytest.param(
                {"uses": SETUP_RUST, "with": {SAVE_INPUT: "false"}}, False, id="false"
            ),
            pytest.param(
                {"uses": SETUP_RUST, "with": {SAVE_INPUT: " TRUE "}},
                True,
                id="padded-and-capitalised",
            ),
        ],
    )
    def test_a_job_without_the_input_counts_as_a_writer(
        self,
        step: WorkflowStep,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """An absent input means the action's default, which is to save.

        Without this, a helper that read a missing input as "not saving"
        would pass the ownership rule on a workflow that had quietly
        gained a second writer, which is precisely the case the rule
        exists for.
        """
        assert _saves_cache(step) is expected

    def test_two_jobs_writing_different_keys_are_not_contended(self) -> None:
        """The rule is about one key with two owners, not about two keys.

        A rule that fired whenever two jobs cached anything would survive
        its own mutation while discriminating nothing, so the narrow
        direction is asserted rather than assumed.
        """
        distinct = _as_workflow(
            {
                "jobs": {
                    "first": {
                        "steps": [
                            {"uses": "actions/cache@abc", "with": {"key": "alpha"}}
                        ]
                    },
                    "second": {
                        "steps": [
                            {"uses": "actions/cache@abc", "with": {"key": "beta"}}
                        ]
                    },
                }
            }
        )
        assert all(len(owners) == 1 for owners in _direct_cache_keys(distinct).values())

        shared = _as_workflow(
            {
                "jobs": {
                    "first": {
                        "steps": [
                            {"uses": "actions/cache@abc", "with": {"key": "alpha"}}
                        ]
                    },
                    "second": {
                        "steps": [
                            {"uses": "actions/cache@abc", "with": {"key": "alpha"}}
                        ]
                    },
                }
            }
        )
        assert _direct_cache_keys(shared)["alpha"] == {"first", "second"}

    def test_a_restore_only_step_is_not_a_writer(self) -> None:
        """Reading a key is not owning it.

        Every job may restore. Counting a restore as a write would make
        the rule unsatisfiable the moment a second job wanted a warm
        registry, which is the outcome this input exists to allow.
        """
        readers = _as_workflow(
            {
                "jobs": {
                    "reader": {
                        "steps": [
                            {
                                "uses": "actions/cache/restore@abc",
                                "with": {"key": "alpha"},
                            }
                        ]
                    },
                }
            }
        )
        assert _direct_cache_keys(readers) == {}
