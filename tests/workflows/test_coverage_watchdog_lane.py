"""Contract that the cargo watchdog is proved, on every pull request.

The proof is only worth having if it runs. Three ways it could stop
running without anyone noticing are covered here: the step could be
pointed at another command while keeping its name, the lane could gain
a paths filter and fall silent on the pull request that breaks the
watchdog from elsewhere, and its `runs-on` could acquire a line break
that GitHub evaluates anyway.

That last one is why the raw declaration is read rather than the parsed
value. A folded scalar whose continuation is indented more deeply than
its first line keeps the break, and the expression then contains a
newline. GitHub evaluates it regardless, so a green run is not evidence.
"""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ
from pathlib import Path

import pytest
import yaml

from . import _watchdog_command_reading as shell

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOW: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "workflows" / "test-coverage-watchdog.yml"
)

#: The script it must be pointed at, which is where the watchdog lives.
COVERAGE_RUNNER: typ.Final[str] = (
    ".github/actions/generate-coverage/scripts/run_rust.py"
)

#: The ceiling every job in the lane carries, in minutes.
LANE_CEILING_MINUTES: typ.Final[int] = 10

#: The runner labels the lane chooses between.
FORK_RUNNER: typ.Final[str] = "ubuntu-latest"
OWN_RUNNER: typ.Final[str] = "ubicloud-standard-2"


#: The shape of the workflow this contract reads, declared rather than
#: inferred. `yaml.safe_load` returns `Any`, and an `Any` flowing through
#: these helpers would switch off every check on every field the
#: assertions below depend on: a workflow that had lost its `jobs`
#: mapping, or whose `runs-on` had become a list, would reach an
#: assertion as something unchecked rather than failing at the boundary.
#:
#: Written in the functional form because `runs-on` is not an identifier.
#: `total=False` throughout, because a step with no `run`, or a job with
#: no `runs-on`, is a workflow this contract has something to say about
#: rather than one it cannot read.
#: A workflow step, narrowed to the fields read here. `if` and
#: `continue-on-error` are kept because either can stop the proof from
#: running or from failing the job, and neither is an identifier, so the
#: functional form applies.
_Step = typ.TypedDict(
    "_Step",
    {"run": str, "if": object, "continue-on-error": object},
    total=False,
)

#: `runs-on` is not an identifier, so this one keeps the functional form.
_Job = typ.TypedDict(
    "_Job",
    {
        "runs-on": str,
        "steps": list[_Step],
        "if": object,
        "continue-on-error": object,
        "timeout-minutes": object,
    },
    total=False,
)

#: The fields that can switch a step or a job off, or keep its failure
#: from reaching the run, copied through from the parse unchanged.
_GUARD_FIELDS: typ.Final[tuple[str, ...]] = ("if", "continue-on-error")


def _as_step(value: object, *, where: str) -> _Step:
    """Return *value* as a step, or fail naming where it came from."""
    match value:
        case dict():
            step = _Step()
        case _:
            msg = f"{where} is not a mapping: {value!r}"
            raise TypeError(msg)
    match value.get("run"):
        case str() as run:
            step["run"] = run
        case _:
            pass
    for field in _GUARD_FIELDS:
        if field in value:
            step[field] = value[field]
    return step


def _is_unguarded(scope: _Step | _Job) -> bool:
    """Return whether *scope* always runs and always reports its failure."""
    return "if" not in scope and scope.get("continue-on-error") in (None, False)


def _as_job(value: object, *, where: str) -> _Job:
    """Return *value* as a job, or fail naming where it came from.

    A `runs-on` that is not a string, such as the list form GitHub also
    accepts, fails here. This lane does not use that form, and reading it
    as a string downstream would compare an expression against a repr.
    """
    match value:
        case {"steps": list() as steps, **rest}:
            parsed = [
                _as_step(step, where=f"{where} step {index}")
                for index, step in enumerate(steps)
            ]
        case dict() as rest:
            parsed = []
        case _:
            msg = f"{where} is not a mapping: {value!r}"
            raise TypeError(msg)
    job = _Job(steps=parsed)
    for field in (*_GUARD_FIELDS, "timeout-minutes"):
        if field in rest:
            job[field] = rest[field]
    match rest.get("runs-on"):
        case None:
            return job
        case str() as runner:
            job["runs-on"] = runner
            return job
        case other:
            msg = f"{where} declares a runs-on that is not a string: {other!r}"
            raise TypeError(msg)


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a mapping declaring one key twice.

    PyYAML keeps the last of two equal keys and says nothing, so a second
    `runs-on`, `on` or `jobs` would silently discard the first before any
    rule here could read it.
    """

    @typ.override
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[typ.Hashable, object]:
        """Construct *node*, raising on the first key it declares twice."""
        duplicate = self._first_duplicate(node, deep=deep)
        if duplicate is not None:
            key, key_node = duplicate
            context = "while constructing a mapping"
            problem = f"found duplicate key {key!r}"
            raise yaml.constructor.ConstructorError(
                context, node.start_mark, problem, key_node.start_mark
            )
        return super().construct_mapping(node, deep=deep)

    def _first_duplicate(
        self, node: yaml.MappingNode, *, deep: bool
    ) -> tuple[object, yaml.Node] | None:
        """Return the first key *node* declares twice, with its node, or None."""
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                return key, key_node
            seen.add(key)
        return None


@dc.dataclass(frozen=True)
class _Workflow:
    """One workflow file, read once: its path, its text, and its parse.

    The text is kept beside the parse because `_raw_runs_on` needs the
    declaration as written, which the parse is exactly what hides.
    """

    path: Path
    text: str
    document: cabc.Mapping[object, object]


def _read(path: Path) -> _Workflow:
    """Read and parse the workflow at *path*, the module's only file access.

    Both failures are reported here, naming the file, so every reader
    below is a pure function of what this returns. The keys are left as
    `object` because one of them is not a string: `on` is read back as
    the boolean `True`, since YAML 1.1 says so and `yaml.safe_load`
    obeys it.

    Raises
    ------
    ValueError
        If the file cannot be read, is not valid YAML, or declares a
        mapping key twice.
    TypeError
        If the document is not a mapping.
    """
    try:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass.
    except (OSError, yaml.YAMLError) as error:
        msg = f"{path} cannot be read as YAML: {error}"
        raise ValueError(msg) from error
    match parsed:
        case dict() as document:
            return _Workflow(path=path, text=text, document=document)
        case other:
            msg = f"{path} is not a mapping: {other!r}"
            raise TypeError(msg)


def _jobs(workflow: _Workflow) -> dict[str, _Job]:
    """Return the workflow's jobs, each narrowed to the shape read here."""
    match workflow.document.get("jobs"):
        case dict() as jobs:
            return {
                str(name): _as_job(job, where=f"job {name}")
                for name, job in jobs.items()
            }
        case other:
            msg = f"{workflow.path} declares no jobs mapping: {other!r}"
            raise TypeError(msg)


def _event_filters(
    event: object, filters: object, *, path: Path
) -> cabc.Mapping[str, object]:
    """Return one event's filters, refusing a value that is neither.

    `None` is the bare spelling, `on: {pull_request:}`, and means no
    filters. A mapping is the filtered spelling. Anything else is
    malformed, and reading it as "no filters" is the dangerous
    direction: a `pull_request` written as a list of branch names would
    filter the lane hard while `test_the_lane_runs_on_every_pull_request`
    reported it unfiltered, which is the exact claim that rule exists to
    make.
    """
    match filters:
        case None:
            return {}
        case dict() as declared:
            return declared
        case other:
            msg = (
                f"{path} declares {event!r} with filters that are neither "
                f"absent nor a mapping: {other!r}"
            )
            raise TypeError(msg)


def _triggers(workflow: _Workflow) -> cabc.Mapping[str, cabc.Mapping[str, object]]:
    """Return the workflow's `on:` mapping, with each event's filters.

    `on` is read back from YAML as the boolean True, because YAML 1.1
    says so and `yaml.safe_load` obeys it. Both spellings are looked up
    rather than one, so this does not depend on which the parser hands
    back.

    An event declared with no filters, which YAML gives back as `None`,
    becomes an empty mapping, so a caller reads "no filters" the same way
    whether the key was written bare or with an empty body.
    """
    document = workflow.document
    for key in (True, "on"):
        if key not in document:
            continue
        match document[key]:
            case None:
                return {}
            case dict() as events:
                return {
                    str(event): _event_filters(event, filters, path=workflow.path)
                    for event, filters in events.items()
                }
            case other:
                msg = (
                    f"{workflow.path} declares triggers that are not a mapping: "
                    f"{other!r}"
                )
                raise TypeError(msg)
    msg = f"{workflow.path} declares no triggers"
    raise AssertionError(msg)


def _raw_runs_on(workflow: _Workflow, job_name: str) -> str:
    """Return a job's `runs-on` as written, including any line break.

    Read from the text rather than the parse, because the parse is what
    hides the defect: a folded scalar with an over-indented continuation
    parses to a string containing a newline, and nothing downstream
    complains.
    """
    pattern = re.compile(
        rf"^  {re.escape(job_name)}:\n(?P<body>(?:^(?:    .*)?\n)*)",
        re.MULTILINE,
    )
    match = pattern.search(workflow.text)
    if match is None:
        msg = f"{workflow.path} declares no job {job_name}"
        raise AssertionError(msg)
    body = match.group("body")
    declaration = re.search(
        r"^    runs-on:(?P<value>.*(?:\n(?:      .*)?)*)", body, re.MULTILINE
    )
    if declaration is None:
        msg = f"{job_name} declares no runs-on"
        raise AssertionError(msg)
    return declaration.group("value")


#: Every key that can stop the trigger firing on some pull requests. A
#: branch filter excludes just as effectively as a path one and is
#: easier to add without thinking about it, so both kinds are named
#: here rather than only the pair this lane was written against.
#:
#: `types` is here too. Its default, `opened`, `synchronize` and
#: `reopened`, is what makes the lane a pre-merge check; `types:
#: [closed]` runs the proof only after the pull request is finished with.
#: A list that kept all three and added more would be harmless, but
#: nothing this lane proves needs another event, so any declaration is
#: refused rather than read for which events it keeps.
TRIGGER_FILTERS: typ.Final[frozenset[str]] = frozenset(
    {"paths", "paths-ignore", "branches", "branches-ignore", "types"}
)

#: The fork fallback, parsed rather than searched for tokens, so that
#: each arm can be tied to the case it serves. The field path is matched
#: exactly: swapping `fork` for a sibling such as `private` changes which
#: pull requests fall back and must not match here.
_FORK_AWARE_RUNS_ON: typ.Final[re.Pattern[str]] = re.compile(
    r"^\$\{\{\s*github\.event\.pull_request\.head\.repo\.fork"
    r"\s*&&\s*'(?P<fork_arm>[^']+)'"
    r"\s*\|\|\s*'(?P<base_arm>[^']+)'\s*\}\}$"
)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize every `job_name` test over the workflow's jobs.

    Done at collection rather than in a decorator, so importing this
    module reads no file and a workflow that cannot be read fails as a
    collection error naming it.
    """
    if "job_name" in metafunc.fixturenames:
        metafunc.parametrize("job_name", sorted(_jobs(_read(WORKFLOW))))


@pytest.fixture(scope="module")
def watchdog() -> _Workflow:
    """Return the watchdog lane's workflow, read once for the module."""
    return _read(WORKFLOW)


class TestCoverageWatchdogLane:
    """The lane that carries the proof, and the ways it could stop running."""

    def test_the_lane_runs_the_proof_script(self, watchdog: _Workflow) -> None:
        """Some step runs the proof, and points it at the coverage runner.

        Asserted as the command. A step that kept the name and ran something
        else would leave the watchdog unproved while the lane stayed green.
        The proof must also be the step's only command, so that its exit
        status is the step's: `set +e`, the proof, then `true` runs the
        proof and reports success whatever it found.
        """
        commands = [
            str(step.get("run", ""))
            for job in _jobs(watchdog).values()
            if _is_unguarded(job)
            for step in job.get("steps", [])
            if _is_unguarded(step)
        ]
        invocations = [
            arguments
            for command in commands
            if (words := shell.sole_command(command)) is not None
            if (arguments := shell.proof_arguments(words)) is not None
        ]

        assert invocations, (
            f"no step in {watchdog.path.name} executes {shell.PROOF_SCRIPT} as its "
            "only command, either directly or through uv run, in a step and job "
            "carrying no `if` and no `continue-on-error`, so the cargo watchdog "
            "is not proved anywhere. A step that passes the path to another "
            "command, such as true or echo, satisfies a substring check and runs "
            "nothing; a guarded one may not run or may fail without failing the "
            "lane; and one running other commands too, such as `set +e` before "
            "it and `true` after, can report success whatever the proof found"
        )
        pointed = [
            arguments
            for arguments in invocations
            if shell.runner_argument(arguments) == COVERAGE_RUNNER
        ]

        assert pointed, (
            f"no execution of {shell.PROOF_SCRIPT} passes --runner "
            f"{COVERAGE_RUNNER}; the proof would then exercise some other "
            "script, or none, and the watchdog the coverage action uses would "
            "go unproved"
        )

    def test_every_job_carries_the_ten_minute_ceiling(
        self, watchdog: _Workflow
    ) -> None:
        """Each job stops at ten minutes, not at GitHub's six hours.

        The proof takes seconds. A watchdog regression that hangs it is
        exactly what this lane exists to catch, and without a ceiling it
        would hold a runner for the platform default before failing.
        """
        ceilings = {
            name: job.get("timeout-minutes") for name, job in _jobs(watchdog).items()
        }

        assert ceilings, f"{watchdog.path.name} declares no jobs"
        assert all(value == LANE_CEILING_MINUTES for value in ceilings.values()), (
            f"{watchdog.path.name} declares job ceilings {ceilings}; each must be "
            f"{LANE_CEILING_MINUTES} minutes"
        )

    def test_the_lane_reads_the_repository_and_nothing_more(
        self, watchdog: _Workflow
    ) -> None:
        """The token is read-only, because the proof writes nothing."""
        permissions = watchdog.document.get("permissions")

        assert permissions == {"contents": "read"}, (
            f"{watchdog.path.name} grants {permissions!r}; the proof needs "
            "`contents: read` and nothing else"
        )

    def test_a_newer_push_supersedes_the_running_proof(
        self, watchdog: _Workflow
    ) -> None:
        """A pull request's newer push cancels the proof still running.

        The lane serves pull requests and a dispatch, never the push to
        `main`, so cancelling spends nothing that a later run does not
        repeat against the newer head.
        """
        concurrency = watchdog.document.get("concurrency")

        assert concurrency == {
            "group": "${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": True,
        }, (
            f"{watchdog.path.name} declares concurrency {concurrency!r}; it must "
            "group by workflow and ref and cancel the run in progress"
        )

    def test_the_lane_runs_on_every_pull_request(self, watchdog: _Workflow) -> None:
        """The lane filters its pull_request trigger by nothing at all.

        A filtered lane falls silent on the pull request that breaks the
        watchdog from somewhere the filter does not name, and a check that
        reports on some pull requests and not others cannot be required.
        Branch filters do this as surely as path ones: `branches: [main]`
        on a `pull_request` trigger matches the base branch, and a pull
        request against any other base then never runs the proof.
        """
        triggers = _triggers(watchdog)

        assert "pull_request" in triggers, (
            f"{watchdog.path.name} does not run on pull_request"
        )
        filters = triggers["pull_request"] or {}
        declared = sorted(set(filters) & TRIGGER_FILTERS)

        assert not declared, (
            f"{watchdog.path.name} filters its pull_request trigger by {declared}, so "
            "the watchdog would go unproved on the pull requests it excludes"
        )

    def test_runs_on_is_one_line(self, watchdog: _Workflow, job_name: str) -> None:
        """No job's `runs-on` carries a line break.

        A folded scalar keeps the break when its continuation is indented
        more deeply than its first line, putting a newline inside the
        expression. GitHub evaluates it regardless and the run goes green,
        so the defect is invisible in CI and has to be refused here.
        """
        raw = _raw_runs_on(watchdog, job_name)
        folded = " ".join(
            part.strip() for part in raw.strip().splitlines() if part.strip()
        )
        parsed = _jobs(watchdog)[job_name]["runs-on"]

        assert "\n" not in str(parsed).strip(), (
            f"{job_name}'s runs-on parses to more than one line: {parsed!r}; keep "
            "the continuation at the same indent as the first line"
        )
        assert folded, f"{job_name} declares an empty runs-on"

    def test_runs_on_maps_each_case_to_its_runner(
        self, watchdog: _Workflow, job_name: str
    ) -> None:
        """A fork gets the GitHub-hosted runner and everything else Ubicloud.

        A pull request from a fork cannot obtain an Ubicloud runner, so a
        lane naming only the Ubicloud label would queue forever on exactly
        the contribution it is meant to check.

        Which arm is which is the whole assertion. Checking that both labels
        and the field path appear somewhere in the expression is satisfied
        just as well by the reversal, which sends forks to the paid runner
        they cannot have and this repository's own pull requests to the
        hosted one. So the expression is parsed and each arm is tied to its
        case.
        """
        parsed = " ".join(str(_jobs(watchdog)[job_name]["runs-on"]).split())
        match = _FORK_AWARE_RUNS_ON.match(parsed)

        assert match is not None, (
            f"{job_name} declares runs-on {parsed!r}, which is not the fork "
            "fallback: it must select its label on "
            "github.event.pull_request.head.repo.fork with a literal label in "
            "each arm"
        )
        assert match.group("fork_arm") == FORK_RUNNER, (
            f"{job_name} sends a fork's pull request to "
            f"{match.group('fork_arm')!r}; it must be {FORK_RUNNER!r}, because a "
            "fork cannot obtain an Ubicloud runner"
        )
        assert match.group("base_arm") == OWN_RUNNER, (
            f"{job_name} sends this repository's own pull requests to "
            f"{match.group('base_arm')!r}; it must be {OWN_RUNNER!r}"
        )
