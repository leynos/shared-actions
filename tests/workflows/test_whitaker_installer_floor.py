"""Contract that no lane asks for a Whitaker installer older than the floor.

The Whitaker installer fetches `cargo-dylint` and `dylint-link` from the
Whitaker repository's own releases, and falls back to compiling them from
crates.io when it cannot. Two commits made the fallback unnecessary for
`dylint-link`, #295 and #300, and both landed in whitaker-installer
0.2.7. Below that version the installer reaches for cargo instead.

On 2026-09-17 that fallback stopped working: resolving `dylint-link`
6.0.1 from crates.io now pulls `cargo-platform` 0.3.3, which requires
rustc 1.91, and this repository pins 1.89. The error is

    rustc 1.89.0 is not supported by the following package:
    cargo-platform@0.3.3 requires rustc 1.91

The break is cache-masked. A job whose installer cache is warm never
runs the fallback and passes while proving nothing, so a green run is
not evidence that a lane is safe. This contract is the evidence: it
reads the version each lane asks for, at rest, where the cause is
legible.

The floor is a version, not a pin. Asking for a newer installer is
always allowed; asking for an older one is the defect.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path, PurePosixPath

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"
INSTALL_WHITAKER_MANIFEST: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "actions" / "install-whitaker" / "action.yml"
)

#: The action's directory, relative to the repository root.
INSTALL_WHITAKER_PATH: typ.Final[PurePosixPath] = PurePosixPath(
    ".github/actions/install-whitaker"
)

#: The two prefixes GitHub reads as this repository: `./` against the
#: checked-out workspace, and `$/` against the running commit, which
#: GitHub now recommends. A lane written either way runs this action, so
#: reading only one would let the other ask for any installer at all.
LOCAL_ACTION_PREFIXES: typ.Final[tuple[str, ...]] = ("./", "$/")

#: The input naming the installer version.
VERSION_INPUT: typ.Final[str] = "installer-version"

#: The oldest installer that installs `dylint-link` from the Whitaker
#: release rather than building it. Raising this floor is a decision:
#: it forces every lane and every consumer to move with it.
INSTALLER_FLOOR: typ.Final[tuple[int, ...]] = (0, 2, 7)

#: Both extensions GitHub accepts for a workflow file. Scanning only
#: `.yml` would let a `.yaml` workflow past every rule here while this
#: module still claimed to be exhaustive.
WORKFLOW_SUFFIXES: typ.Final[tuple[str, ...]] = (".yml", ".yaml")

#: A bare `env.NAME`, the one expression shape these workflows use for
#: the version. Anything else is left unresolved and fails loudly rather
#: than being quietly accepted as "probably fine".
_ENV_EXPRESSION: typ.Final[re.Pattern[str]] = re.compile(
    r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)

#: One to three numeric components, which is what the action's own input
#: validation accepts.
_VERSION: typ.Final[re.Pattern[str]] = re.compile(r"\A\d+(?:\.\d+){0,2}\Z")


def _workflow_names() -> list[str]:
    """Return every workflow file name, sorted."""
    return sorted(
        path.name
        for path in WORKFLOWS_DIRECTORY.iterdir()
        if path.suffix in WORKFLOW_SUFFIXES and path.is_file()
    )


def _mapping(value: object, *, subject: str) -> dict[object, object]:
    """Return *value* if it is a mapping, and fail naming *subject* if not.

    The one place parsed YAML is narrowed. `yaml.safe_load` returns
    `Any`, and letting that flow onward would let a reader call a
    mapping method on a list without any diagnostic, so every value
    this module indexes into passes through here first.
    """
    match value:
        case dict():
            return value
        case _:
            msg = f"{subject} is not a mapping: {value!r}"
            raise TypeError(msg)


def _load(path: Path) -> dict[object, object]:
    """Return the parsed YAML document at *path*."""
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), subject=str(path))


def _environment(scope: object) -> dict[object, object]:
    """Return the `env` mapping *scope* declares, or an empty one.

    A scope that is not a mapping, and one whose `env` is not a mapping,
    both declare nothing this reader can resolve a name against, so both
    answer empty rather than failing: an `env` written as a list is a
    workflow defect for the schema to report, not a reason this rule
    cannot read the version beside it.
    """
    match scope:
        case {"env": dict() as environment}:
            return environment
        case _:
            return {}


def _resolve(
    value: str,
    *,
    step: cabc.Mapping[object, object],
    job: cabc.Mapping[object, object],
    workflow: cabc.Mapping[object, object],
) -> str:
    """Resolve a bare `env.NAME` against the scopes GitHub would search.

    Step, then job, then workflow, and no further. Searching every job's
    environment would let a name defined in an unrelated job decide this
    lane's version, which is not what the runner does.
    """
    match = _ENV_EXPRESSION.fullmatch(value.strip())
    if match is None:
        return value.strip()
    name = match.group(1)
    for scope in (step, job, workflow):
        environment = _environment(scope)
        if name in environment:
            resolved = environment[name]
            return "" if resolved is None else str(resolved).strip()
    return value.strip()


def _version(raw: str) -> tuple[int, ...]:
    """Return *raw* as a comparable tuple, padded to three components."""
    parts = [int(part) for part in raw.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def _jobs(
    workflow: cabc.Mapping[object, object],
) -> cabc.Iterator[tuple[str, dict[object, object]]]:
    """Yield each job the workflow declares as a mapping, with its id."""
    jobs = workflow.get("jobs")
    if jobs is None:
        return
    for job_id, job in _mapping(jobs, subject="the workflow's jobs").items():
        match job:
            case dict():
                yield str(job_id), job
            case _:
                continue


def _steps(job: cabc.Mapping[object, object]) -> cabc.Iterator[dict[object, object]]:
    """Yield each step the job declares as a mapping.

    A `steps` value that is not a list fails rather than reading as a job
    with no steps, which would hide any install step it holds.
    """
    match job.get("steps"):
        case None:
            return
        case list() as steps:
            yield from (step for step in steps if isinstance(step, dict))
        case other:
            msg = f"a job's steps are not a list: {other!r}"
            raise TypeError(msg)


def _installs_whitaker(uses: object) -> bool:
    """Return whether *uses* names this repository's Whitaker action.

    Matched by shape, not by spelling: strip either local prefix and
    compare what remains as a path, so `./` and `$/` both count and a
    trailing slash does not hide a lane. A reference to another
    repository, including this one at a pinned ref, runs whatever that
    ref holds, which is not the action this contract guards.
    """
    match uses:
        case str() if uses.startswith(LOCAL_ACTION_PREFIXES):
            return PurePosixPath(uses[2:]) == INSTALL_WHITAKER_PATH
        case _:
            return False


def _supplied_version(step: cabc.Mapping[object, object]) -> str | None:
    """Return the installer version this step supplies, or None.

    None covers both a step that installs something else and a step that
    installs Whitaker without naming a version. The second takes the
    action's default, which
    `test_the_action_default_is_at_or_above_the_floor` covers instead.
    """
    if not _installs_whitaker(step.get("uses")):
        return None
    inputs = step.get("with")
    if inputs is None:
        return None
    supplied = _mapping(inputs, subject="the step's with").get(VERSION_INPUT)
    return None if supplied is None else str(supplied)


def _lanes(
    name: str, workflow: cabc.Mapping[object, object]
) -> cabc.Iterator[tuple[str, str, str]]:
    """Yield each lane in *workflow* that installs Whitaker naming a version.

    Each entry is the workflow file, the job id, and the resolved
    `installer-version`.
    """
    for job_id, job in _jobs(workflow):
        yield from (
            (name, job_id, _resolve(supplied, step=step, job=job, workflow=workflow))
            for step in _steps(job)
            if (supplied := _supplied_version(step)) is not None
        )


def _install_whitaker_steps() -> list[tuple[str, str, str]]:
    """Return every lane in the repository that installs Whitaker."""
    return [
        lane
        for name in _workflow_names()
        for lane in _lanes(name, _load(WORKFLOWS_DIRECTORY / name))
    ]


def _identifier(*parts: str) -> str:
    """Return a readable pytest id for *parts*."""
    return "::".join(parts)


def _assert_at_or_above_floor(requested: str, *, subject: str) -> None:
    """Fail unless *subject* asks for an installer at or above the floor.

    Shared by the lane rule and the action-default rule, because the
    reason is the same in both places: a caller that omits the input is
    exposed by exactly the version the manifest names.
    """
    assert _VERSION.fullmatch(requested), (
        f"{subject} asks for installer-version {requested!r}, which is not a "
        "version this rule can read; it must be one to three numeric "
        "components, resolved from a bare env.NAME at most"
    )
    floor = ".".join(str(part) for part in INSTALLER_FLOOR)
    assert _version(requested) >= INSTALLER_FLOOR, (
        f"{subject} asks for Whitaker installer {requested}, below the "
        f"{floor} floor. Below {floor} the installer builds dylint-link from "
        "crates.io instead of taking the published artefact, and that build "
        "needs a newer rustc than this repository pins. It fails only on a "
        "cold installer cache, so a green run does not clear it"
    )


class TestTheInstallerFloor:
    """Which installer each lane asks for, and what the action defaults to.

    The three rules here are one subject: no caller of this repository's
    Whitaker action may end up on an installer that builds dylint-link
    rather than installing it. A lane naming a version is covered by the
    second, a caller naming none by the third, and the first keeps both
    honest by refusing a repository with no install step at all.
    """

    def test_some_lane_installs_whitaker(self) -> None:
        """At least one lane installs Whitaker, so the rule below has a subject.

        Without this, deleting every install step would satisfy the floor by
        having nothing to check, and the lint gate would go with it.
        """
        assert _install_whitaker_steps(), (
            "no workflow supplies an installer-version to "
            f"{INSTALL_WHITAKER_PATH}; either the lint lane has gone or every "
            "step now relies on the action default, and this rule is checking "
            "nothing"
        )

    @pytest.mark.parametrize(
        ("workflow", "job_id", "requested"),
        _install_whitaker_steps(),
        ids=lambda value: str(value),
    )
    def test_no_lane_asks_for_an_installer_below_the_floor(
        self, workflow: str, job_id: str, requested: str
    ) -> None:
        """Every lane asks for an installer that installs dylint-link, not builds it.

        An older installer compiles `dylint-link` from crates.io, which since
        2026-09-17 needs a rustc newer than this repository pins. The job
        fails only when its installer cache is cold, so the version is
        asserted here rather than waited for.
        """
        _assert_at_or_above_floor(requested, subject=_identifier(workflow, job_id))

    def test_the_action_default_is_at_or_above_the_floor(self) -> None:
        """The action's own default carries the floor for callers that omit it.

        A caller supplying no `installer-version` takes this value, and most
        consumers do. A floor the workflows respect while the default sits
        below it would leave every such consumer exposed.
        """
        manifest = _load(INSTALL_WHITAKER_MANIFEST)
        inputs = _mapping(manifest.get("inputs"), subject="the manifest's inputs")
        version_input = _mapping(inputs.get(VERSION_INPUT), subject=VERSION_INPUT)
        default = str(version_input.get("default")).strip()

        _assert_at_or_above_floor(
            default, subject=f"{INSTALL_WHITAKER_MANIFEST.name}'s own default"
        )


class TestTheFloorComparison:
    """How two versions are ordered, which the rules above depend on."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("0.2.7", True, id="the-floor-itself"),
            pytest.param("0.2.8", True, id="above-it"),
            pytest.param("0.3", True, id="two-components-above"),
            pytest.param("1", True, id="one-component-above"),
            pytest.param("0.2.6", False, id="the-version-that-broke"),
            pytest.param("0.2", False, id="two-components-below"),
            pytest.param("0.10.0", True, id="not-compared-as-text"),
            pytest.param("0.2.10", True, id="patch-not-compared-as-text"),
        ],
    )
    def test_the_floor_comparison_is_numeric(
        self,
        raw: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Versions compare component by component, never as strings.

        Both directions. A string comparison would place `0.10.0` below
        `0.2.7` and reject a lane that is comfortably ahead of the floor,
        and padding matters too: `0.2` is `0.2.0`, which is below it.
        """
        assert (_version(raw) >= INSTALLER_FLOOR) is expected, (
            f"{raw} should be read as {'at or above' if expected else 'below'} "
            f"the floor {INSTALLER_FLOOR}"
        )


class TestTheActionReference:
    """Which `uses:` values this contract reads as the Whitaker action."""

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/actions/install-whitaker", True, id="workspace"),
            pytest.param("$/.github/actions/install-whitaker", True, id="self"),
            pytest.param("./.github/actions/install-whitaker/", True, id="slash"),
            pytest.param("$/.github/actions/install-mdtablefix", False, id="other"),
            pytest.param(
                "./.github/actions/install-whitaker-next", False, id="longer-name"
            ),
            pytest.param(
                "leynos/shared-actions/.github/actions/install-whitaker@v1",
                False,
                id="pinned-remote",
            ),
            pytest.param(None, False, id="run-step"),
        ],
    )
    def test_the_action_is_recognized_by_shape(
        self,
        uses: str | None,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Both local prefixes name the action, and nothing else does.

        GitHub runs `$/` and `./` against the same directory, so a lane
        written either way must be read; a pinned remote reference runs
        another commit's action and is outside this contract.
        """
        assert _installs_whitaker(uses) is expected, (
            f"{uses!r} should {'' if expected else 'not '}be read as the "
            "Whitaker action"
        )

    def test_a_self_reference_below_the_floor_is_refused(self) -> None:
        """A `$/` lane asking for 0.2.6 reaches the floor rule and fails it.

        The recognition case above could pass while the lane reader
        compared the spelling some other way, so this drives a whole
        workflow through the reader and the assertion together.
        """
        workflow: dict[object, object] = {
            "jobs": {
                "lint": {
                    "steps": [
                        {
                            "uses": "$/.github/actions/install-whitaker",
                            "with": {VERSION_INPUT: "0.2.6"},
                        }
                    ]
                }
            }
        }

        lanes = list(_lanes("ci.yml", workflow))

        assert lanes == [("ci.yml", "lint", "0.2.6")], lanes
        with pytest.raises(AssertionError, match=re.escape("below the 0.2.7 floor")):
            _assert_at_or_above_floor(lanes[0][2], subject="ci.yml::lint")


class TestTheYamlBoundary:
    """What the reader does with a document whose shape has drifted."""

    @pytest.mark.parametrize(
        "workflow",
        [
            pytest.param({"jobs": ["lint"]}, id="jobs-as-a-list"),
            pytest.param(
                {"jobs": {"lint": {"steps": "make lint"}}}, id="steps-as-text"
            ),
            pytest.param(
                {
                    "jobs": {
                        "lint": {
                            "steps": [
                                {
                                    "uses": "./.github/actions/install-whitaker",
                                    "with": ["installer-version"],
                                }
                            ]
                        }
                    }
                },
                id="with-as-a-list",
            ),
        ],
    )
    def test_a_drifted_shape_fails_rather_than_reading_as_empty(
        self, workflow: dict[object, object]
    ) -> None:
        """A container of the wrong type raises instead of hiding its lanes.

        Reading any of these as "no jobs", "no steps" or "no inputs" would
        drop an install step from the floor rule without a word.
        """
        with pytest.raises(TypeError):
            list(_lanes("ci.yml", workflow))
