"""Contract that Merman CLI comes from a pinned archive, not a source build.

`ci.yml` used to install `merman-cli` with `cargo install`, behind an
`actions/cache` entry that saved and then never restored. The build was
therefore not occasional but constant, and at 5m 42s it was the largest
single step in the repository's slowest job. It also broke the estate's
rule that tools arrive as pinned, checksum-verified release archives.
Issue #483 carries the log extracts from both measured runs.

The rule has two halves and this module holds `ci.yml` to both. One test
says no step builds the tool from source. The other says a step really
does install it from the manifest, so that satisfying the first by
deleting the install altogether is not an option. A third ties the
version the workflow asks for to a version the manifest actually pins,
because an unpinned version reintroduces the unverified download this
change exists to remove.

Steps are matched by what they invoke, never by their names. A step
named "Install Merman CLI" that builds from source is exactly the defect
this rule prevents, and a differently named step that does the same is
no better.
"""

from __future__ import annotations

import re
import tomllib
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
TOOL_MANIFEST: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "tool-manifest.toml"

#: The tool as the manifest names it, which is also what the workflow asks for.
TOOL_NAME: typ.Final[str] = "merman-cli"

#: The action that installs a manifest tool from a digest-verified archive.
INSTALL_TOOL_ACTION: typ.Final[str] = "./.github/actions/install-tool"

#: The input naming which manifest tool an `install-tool` step installs.
TOOL_INPUT: typ.Final[str] = "tool"

#: The input naming the version it installs.
VERSION_INPUT: typ.Final[str] = "version"

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


#: A `cargo install` of this tool, as a command.
#:
#: Leading whitespace is consumed after the line anchor, because YAML strips
#: only a block's common indentation: a build inside a conditional or a loop
#: body stays indented and runs exactly as plainly as one at column zero.
#:
#: `cargo` may carry a toolchain selector such as `+1.95.0`, and `install`
#: may be followed by any number of options before the crate name, so both
#: are allowed for between the words. Without the options allowance,
#: `cargo install --locked merman-cli` would restore the source build with
#: this contract still green.
#:
#: The crate name may carry a version, because `cargo install
#: merman-cli@0.7.0` is a source build by another spelling, and the match
#: ends on a lookahead so that a command terminated by `;` counts as much as
#: one terminated by a space.
#:
#: The pattern is assembled from module-level literals rather than from
#: anything a workflow supplies, so there is no input here to drive
#: backtracking.
_CARGO_INSTALL: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^[ \t]*|[;&|]\s*)cargo(?:\s+\+\S+)?\s+install\s+(?:-\S+(?:\s+\S+)?\s+)*"
    + re.escape(TOOL_NAME)
    + r"(?:@[^\s;&|]+)?(?=\s|[;&|]|$)",
    re.MULTILINE,
)


def _as_workflow(document: object) -> Workflow:
    """Narrow a parsed YAML document to the shape these tests read.

    The check is at the boundary and deliberate. `yaml.safe_load` returns
    `Any`, so without it every helper below would be reading unchecked
    fields off whatever the file happened to contain, and a workflow that
    had lost its `jobs` mapping would surface as an empty result rather
    than as an error.
    """
    if not isinstance(document, dict):
        msg = f"{CI_WORKFLOW} is not a mapping"
        raise TypeError(msg)
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        msg = f"{CI_WORKFLOW} has no jobs mapping"
        raise TypeError(msg)
    return typ.cast("Workflow", document)


@pytest.fixture(scope="module")
def ci_document() -> Workflow:
    """Return the parsed `ci.yml`, narrowed to the shape read here."""
    return _as_workflow(yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def manifest_versions() -> cabc.Mapping[str, frozenset[str]]:
    """Return the versions the tool manifest pins, keyed by tool name."""
    document = tomllib.loads(TOOL_MANIFEST.read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for entry in document.get("tool", []):
        versions.setdefault(str(entry["name"]), set()).add(str(entry["version"]))
    return {name: frozenset(found) for name, found in versions.items()}


def _all_steps(document: Workflow) -> list[WorkflowStep]:
    """Return every step of every job, so no job can hide a source build."""
    return [
        step
        for job in document.get("jobs", {}).values()
        for step in job.get("steps", [])
    ]


def _install_tool_steps(document: Workflow) -> list[WorkflowStep]:
    """Return the steps that install this tool through `install-tool`."""
    return [
        step
        for step in _all_steps(document)
        if step.get("uses") == INSTALL_TOOL_ACTION
        and str(step.get("with", {}).get(TOOL_INPUT, "")) == TOOL_NAME
    ]


def _resolve_workflow_expression(value: str, document: Workflow) -> str:
    """Resolve a bare `env.NAME` expression against the workflow's job envs.

    Only the one shape the workflow uses is resolved. Anything else is
    returned unchanged, so an unrecognised expression fails the version
    check loudly rather than being quietly accepted.
    """
    match = re.fullmatch(
        r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", value.strip()
    )
    if match is None:
        return value
    name = match.group(1)
    for job in document.get("jobs", {}).values():
        environment = typ.cast("dict[str, object]", job).get("env")
        if isinstance(environment, dict) and name in environment:
            return str(environment[name])
    return value


class TestMermanReleaseArchive:
    """Where `ci.yml` gets Merman CLI, and where it must not get it."""

    def test_no_step_builds_merman_from_source(self, ci_document: Workflow) -> None:
        """No step in any job compiles the tool with `cargo install`.

        This is the defect the change removed. It reads as an ordinary
        install step and costs minutes on every pull request, because the
        cache meant to spare it never restored.
        """
        offenders = [
            step.get("name", "<unnamed>")
            for step in _all_steps(ci_document)
            if _CARGO_INSTALL.search(str(step.get("run", "")))
        ]
        assert not offenders, (
            f"ci.yml builds {TOOL_NAME} from source in {offenders}; install it "
            f"from the pinned archive with {INSTALL_TOOL_ACTION} instead"
        )

    def test_a_step_installs_merman_from_the_manifest(
        self, ci_document: Workflow
    ) -> None:
        """The workflow still installs the tool, through `install-tool`.

        This is the other half of the rule. Without it the source-build
        ban above is satisfied by removing the install entirely, which
        would leave the Mermaid validation step with no binary to run.
        """
        steps = _install_tool_steps(ci_document)
        assert steps, (
            f"ci.yml installs {TOOL_NAME} in no step; `make nixie` has no binary to run"
        )

    def test_the_requested_version_is_one_the_manifest_pins(
        self,
        ci_document: Workflow,
        manifest_versions: cabc.Mapping[str, frozenset[str]],
    ) -> None:
        """The version the workflow asks for is pinned in the manifest.

        `install-tool` refuses a version it cannot resolve, so a drifted
        version fails the job rather than downloading something
        unverified. This asserts it at rest, where the cause is legible,
        instead of waiting for a red run to say it.
        """
        pinned = manifest_versions.get(TOOL_NAME, frozenset())
        requested = {
            _resolve_workflow_expression(
                str(step.get("with", {}).get(VERSION_INPUT, "")), ci_document
            )
            for step in _install_tool_steps(ci_document)
        }
        unpinned = requested - pinned
        assert not unpinned, (
            f"ci.yml asks for {TOOL_NAME} {sorted(unpinned)}, which the tool "
            f"manifest does not pin; it pins {sorted(pinned)}"
        )

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            pytest.param(f"cargo install {TOOL_NAME}", True, id="plain"),
            pytest.param(
                f"cargo +1.95.0 install {TOOL_NAME} --locked", True, id="toolchain"
            ),
            pytest.param(
                f"cargo install --locked {TOOL_NAME}", True, id="options-first"
            ),
            pytest.param(f"    cargo install {TOOL_NAME}", True, id="indented"),
            pytest.param(f"\tcargo install {TOOL_NAME}", True, id="tab-indented"),
            pytest.param(
                f"if true; then\n  cargo install {TOOL_NAME}\nfi",
                True,
                id="indented-in-a-conditional-body",
            ),
            pytest.param(f"cargo install {TOOL_NAME}@0.7.0", True, id="version-pinned"),
            pytest.param(
                f"cargo install {TOOL_NAME}; echo done", True, id="separator-terminated"
            ),
            pytest.param(
                "cargo install some-other-crate --locked", False, id="another-crate"
            ),
            pytest.param(
                f"cargo install {TOOL_NAME}-extras", False, id="longer-crate-name"
            ),
            pytest.param(
                f"# cargo install {TOOL_NAME} was removed", False, id="in-a-comment"
            ),
            pytest.param(f"uv tool install {TOOL_NAME}", False, id="not-cargo"),
        ],
    )
    def test_the_source_build_pattern_matches_what_would_break(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """The pattern catches every spelling of the build, and nothing else.

        Each positive case is a real way to compile this crate: indented
        inside a block, pinned with `@version`, or terminated by `;`
        rather than by whitespace. Each would have evaded an earlier
        version of this pattern while the contract stayed green.

        The negative cases carry the same weight. A rule that fired on
        every `cargo install`, or on a crate whose name merely starts
        with this one, would survive its own mutation while
        discriminating nothing.
        """
        assert bool(_CARGO_INSTALL.search(script)) is expected
