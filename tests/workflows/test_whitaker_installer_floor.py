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
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows"
INSTALL_WHITAKER_MANIFEST: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "actions" / "install-whitaker" / "action.yml"
)

#: The action as a workflow names it.
INSTALL_WHITAKER_ACTION: typ.Final[str] = "./.github/actions/install-whitaker"

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


def _load(path: Path) -> dict[str, typ.Any]:
    """Return the parsed YAML document at *path*."""
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        msg = f"{path} is not a mapping"
        raise TypeError(msg)
    return parsed


def _environment(scope: object) -> dict[str, object]:
    """Return the `env` mapping *scope* declares, or an empty one."""
    if not isinstance(scope, dict):
        return {}
    environment = scope.get("env")
    return environment if isinstance(environment, dict) else {}


def _resolve(
    value: str,
    *,
    step: cabc.Mapping[str, typ.Any],
    job: cabc.Mapping[str, typ.Any],
    workflow: cabc.Mapping[str, typ.Any],
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
    workflow: cabc.Mapping[str, typ.Any],
) -> cabc.Iterator[tuple[str, dict[str, typ.Any]]]:
    """Yield each job the workflow declares as a mapping, with its id."""
    for job_id, job in (workflow.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield str(job_id), job


def _steps(job: cabc.Mapping[str, typ.Any]) -> cabc.Iterator[dict[str, typ.Any]]:
    """Yield each step the job declares as a mapping."""
    for step in job.get("steps") or []:
        if isinstance(step, dict):
            yield step


def _supplied_version(step: cabc.Mapping[str, typ.Any]) -> str | None:
    """Return the installer version this step supplies, or None.

    None covers both a step that installs something else and a step that
    installs Whitaker without naming a version. The second takes the
    action's default, which
    `test_the_action_default_is_at_or_above_the_floor` covers instead.
    """
    if step.get("uses") != INSTALL_WHITAKER_ACTION:
        return None
    supplied = (step.get("with") or {}).get(VERSION_INPUT)
    return None if supplied is None else str(supplied)


def _install_whitaker_steps() -> list[tuple[str, str, str]]:
    """Return every lane that installs Whitaker, with the version it asks for.

    Each entry is the workflow file, the job id, and the resolved
    `installer-version`.
    """
    found: list[tuple[str, str, str]] = []
    for name in _workflow_names():
        workflow = _load(WORKFLOWS_DIRECTORY / name)
        for job_id, job in _jobs(workflow):
            found.extend(
                (
                    name,
                    job_id,
                    _resolve(supplied, step=step, job=job, workflow=workflow),
                )
                for step in _steps(job)
                if (supplied := _supplied_version(step)) is not None
            )
    return found


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


def test_some_lane_installs_whitaker() -> None:
    """At least one lane installs Whitaker, so the rule below has a subject.

    Without this, deleting every install step would satisfy the floor by
    having nothing to check, and the lint gate would go with it.
    """
    assert _install_whitaker_steps(), (
        "no workflow supplies an installer-version to "
        f"{INSTALL_WHITAKER_ACTION}; either the lint lane has gone or every "
        "step now relies on the action default, and this rule is checking "
        "nothing"
    )


@pytest.mark.parametrize(
    ("workflow", "job_id", "requested"),
    _install_whitaker_steps(),
    ids=lambda value: str(value),
)
def test_no_lane_asks_for_an_installer_below_the_floor(
    workflow: str, job_id: str, requested: str
) -> None:
    """Every lane asks for an installer that installs dylint-link, not builds it.

    An older installer compiles `dylint-link` from crates.io, which since
    2026-09-17 needs a rustc newer than this repository pins. The job
    fails only when its installer cache is cold, so the version is
    asserted here rather than waited for.
    """
    _assert_at_or_above_floor(requested, subject=_identifier(workflow, job_id))


def test_the_action_default_is_at_or_above_the_floor() -> None:
    """The action's own default carries the floor for callers that omit it.

    A caller supplying no `installer-version` takes this value, and most
    consumers do. A floor the workflows respect while the default sits
    below it would leave every such consumer exposed.
    """
    manifest = _load(INSTALL_WHITAKER_MANIFEST)
    default = str(manifest["inputs"][VERSION_INPUT]["default"]).strip()

    _assert_at_or_above_floor(
        default, subject=f"{INSTALL_WHITAKER_MANIFEST.name}'s own default"
    )


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
