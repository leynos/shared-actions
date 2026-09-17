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

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOW: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "workflows" / "test-coverage-watchdog.yml"
)

#: The script that carries the proof. Asserted as the command the step
#: runs rather than as the step's name, because a step named "Prove the
#: cargo watchdog" that runs something else is not this rule, and a
#: differently named one that runs this script is.
PROOF_SCRIPT: typ.Final[str] = "workflow_scripts/prove_cargo_watchdog.py"

#: The script it must be pointed at, which is where the watchdog lives.
COVERAGE_RUNNER: typ.Final[str] = (
    ".github/actions/generate-coverage/scripts/run_rust.py"
)

#: The runner labels the lane chooses between.
FORK_RUNNER: typ.Final[str] = "ubuntu-latest"
OWN_RUNNER: typ.Final[str] = "ubicloud-standard-2"


def _document() -> dict[str, typ.Any]:
    """Return the parsed workflow."""
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        msg = f"{WORKFLOW} is not a mapping"
        raise TypeError(msg)
    return parsed


def _jobs() -> dict[str, dict[str, typ.Any]]:
    """Return the workflow's jobs."""
    return _document()["jobs"]


def _triggers() -> dict[str, typ.Any]:
    """Return the workflow's `on:` mapping.

    `on` is read back from YAML as the boolean True, because YAML 1.1
    says so and `yaml.safe_load` obeys it. Both spellings are looked up
    rather than one, so this does not depend on which the parser hands
    back.
    """
    document = _document()
    for key in (True, "on"):
        if key in document:
            return document[key] or {}
    msg = f"{WORKFLOW} declares no triggers"
    raise AssertionError(msg)


def _raw_runs_on(job_name: str) -> str:
    """Return a job's `runs-on` as written, including any line break.

    Read from the text rather than the parse, because the parse is what
    hides the defect: a folded scalar with an over-indented continuation
    parses to a string containing a newline, and nothing downstream
    complains.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^  {re.escape(job_name)}:\n(?P<body>(?:^(?:    .*)?\n)*)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        msg = f"{WORKFLOW} declares no job {job_name}"
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
TRIGGER_FILTERS: typ.Final[frozenset[str]] = frozenset(
    {"paths", "paths-ignore", "branches", "branches-ignore"}
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


class TestCoverageWatchdogLane:
    """The lane that carries the proof, and the ways it could stop running."""

    def test_the_lane_runs_the_proof_script(self) -> None:
        """Some step runs the proof, and points it at the coverage runner.

        Asserted as the command. A step that kept the name and ran something
        else would leave the watchdog unproved while the lane stayed green.
        """
        commands = [
            str(step.get("run", ""))
            for job in _jobs().values()
            for step in job.get("steps", [])
        ]
        running_the_proof = [command for command in commands if PROOF_SCRIPT in command]

        assert running_the_proof, (
            f"no step in {WORKFLOW.name} runs {PROOF_SCRIPT}, so the cargo "
            "watchdog is not proved anywhere"
        )
        assert any(COVERAGE_RUNNER in command for command in running_the_proof), (
            f"the proof is not pointed at {COVERAGE_RUNNER}, so it would not "
            "exercise the watchdog the coverage action uses"
        )

    def test_the_lane_runs_on_every_pull_request(self) -> None:
        """The lane filters its pull_request trigger by nothing at all.

        A filtered lane falls silent on the pull request that breaks the
        watchdog from somewhere the filter does not name, and a check that
        reports on some pull requests and not others cannot be required.
        Branch filters do this as surely as path ones: `branches: [main]`
        on a `pull_request` trigger matches the base branch, and a pull
        request against any other base then never runs the proof.
        """
        triggers = _triggers()

        assert "pull_request" in triggers, (
            f"{WORKFLOW.name} does not run on pull_request"
        )
        filters = triggers["pull_request"] or {}
        declared = sorted(set(filters) & TRIGGER_FILTERS)

        assert not declared, (
            f"{WORKFLOW.name} filters its pull_request trigger by {declared}, so "
            "the watchdog would go unproved on the pull requests it excludes"
        )

    @pytest.mark.parametrize("job_name", sorted(_jobs()))
    def test_runs_on_is_one_line(self, job_name: str) -> None:
        """No job's `runs-on` carries a line break.

        A folded scalar keeps the break when its continuation is indented
        more deeply than its first line, putting a newline inside the
        expression. GitHub evaluates it regardless and the run goes green,
        so the defect is invisible in CI and has to be refused here.
        """
        raw = _raw_runs_on(job_name)
        folded = " ".join(
            part.strip() for part in raw.strip().splitlines() if part.strip()
        )
        parsed = _jobs()[job_name]["runs-on"]

        assert "\n" not in str(parsed).strip(), (
            f"{job_name}'s runs-on parses to more than one line: {parsed!r}; keep "
            "the continuation at the same indent as the first line"
        )
        assert folded, f"{job_name} declares an empty runs-on"

    @pytest.mark.parametrize("job_name", sorted(_jobs()))
    def test_runs_on_maps_each_case_to_its_runner(self, job_name: str) -> None:
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
        parsed = " ".join(str(_jobs()[job_name]["runs-on"]).split())
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
