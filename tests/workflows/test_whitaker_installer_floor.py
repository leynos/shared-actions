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

#: Both extensions GitHub accepts for a workflow file, compared without
#: regard to case. Scanning only `.yml` would let a `.yaml` workflow past
#: every rule here while this module still claimed to be exhaustive.
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


def _workflow_paths(directory: Path) -> list[Path]:
    """Return every workflow file in *directory*, sorted by name."""
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.suffix.lower() in WORKFLOW_SUFFIXES and path.is_file()
        ),
        key=lambda path: path.name,
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
    """Return the parsed YAML document at *path*.

    The one place this module touches the filesystem or the parser, so
    both of their failures are reported here, naming the file, rather
    than surfacing from whichever rule happened to read it first.

    Raises
    ------
    ValueError
        If the file cannot be read or is not valid YAML.
    TypeError
        If the document is not a mapping.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        msg = f"{path} cannot be read as YAML: {error}"
        raise ValueError(msg) from error
    return _mapping(document, subject=str(path))


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


def _install_whitaker_steps(directory: Path) -> list[tuple[str, str, str]]:
    """Return every lane in *directory*'s workflows that installs Whitaker."""
    return [
        lane
        for path in _workflow_paths(directory)
        for lane in _lanes(path.name, _load(path))
    ]


def _action_default(manifest: Path) -> str:
    """Return the `installer-version` default the action manifest declares."""
    inputs = _mapping(_load(manifest).get("inputs"), subject=f"{manifest}'s inputs")
    version_input = _mapping(inputs.get(VERSION_INPUT), subject=VERSION_INPUT)
    return str(version_input.get("default")).strip()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize the lane rule over the repository's Whitaker lanes.

    Done at collection rather than in a decorator, so importing this
    module reads no file and an unreadable workflow fails as a collection
    error naming it.
    """
    if "requested" in metafunc.fixturenames:
        metafunc.parametrize(
            ("workflow", "job_id", "requested"),
            _install_whitaker_steps(WORKFLOWS_DIRECTORY),
            ids=str,
        )


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
        assert _install_whitaker_steps(WORKFLOWS_DIRECTORY), (
            "no workflow supplies an installer-version to "
            f"{INSTALL_WHITAKER_PATH}; either the lint lane has gone or every "
            "step now relies on the action default, and this rule is checking "
            "nothing"
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
        _assert_at_or_above_floor(
            _action_default(INSTALL_WHITAKER_MANIFEST),
            subject=f"{INSTALL_WHITAKER_MANIFEST.name}'s own default",
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
