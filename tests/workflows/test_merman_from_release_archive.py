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
from hypothesis import given
from hypothesis import strategies as st

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
    {
        "name": str,
        "uses": str,
        "run": str,
        "with": "dict[str, str]",
        "env": "dict[str, object]",
    },
    total=False,
)


class WorkflowJob(typ.TypedDict, total=False):
    """One job of a workflow, as much of it as these tests read."""

    steps: list[WorkflowStep]
    env: dict[str, object]


class Workflow(typ.TypedDict, total=False):
    """A parsed workflow document."""

    jobs: dict[str, WorkflowJob]
    env: dict[str, object]


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
#: Shell keywords a command can sit behind without being any less run.
#: `if true; then cargo install merman-cli; fi` compiles the crate exactly
#: as plainly as a bare command does, and the separator alternative above
#: stops at the `;`, which leaves `then` between it and `cargo`. The
#: keywords are matched as whole words, so a crate or a path ending in one
#: of them is not read as a keyword.
_SHELL_PREFIX_KEYWORDS: typ.Final[tuple[str, ...]] = (
    "if",
    "then",
    "else",
    "elif",
    "do",
    "while",
    "until",
)

#: A command may carry environment assignments of its own, and they change
#: nothing about whether it runs. `RUSTUP_TOOLCHAIN=1.95.0 cargo install
#: merman-cli --locked` is exactly the source build this contract refuses,
#: written the way a step that wanted a particular toolchain would write
#: it, and without this allowance the assignment hid the command from the
#: pattern. The value stops at whitespace or a separator, so the
#: assignment cannot swallow the rest of the line.
_SHELL_ASSIGNMENT: typ.Final[str] = r"(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|]*\s+)*"

#: The pattern is assembled from module-level literals rather than from
#: anything a workflow supplies, so there is no input here to drive
#: backtracking.
_CARGO_INSTALL: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^[ \t]*|[;&|]\s*|\b(?:"
    + "|".join(_SHELL_PREFIX_KEYWORDS)
    + r")\s+)"
    + _SHELL_ASSIGNMENT
    + r"cargo(?:\s+\+\S+)?\s+install\s+(?:-\S+(?:\s+\S+)?\s+)*"
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


def _install_tool_steps(
    document: Workflow,
) -> list[tuple[WorkflowJob, WorkflowStep]]:
    """Return each `install-tool` step for this tool, with its owning job.

    The job travels with the step because an `env.NAME` in the step's
    inputs resolves in that job and nowhere else.
    """
    return [
        (job, step)
        for job in document.get("jobs", {}).values()
        for step in job.get("steps", [])
        if step.get("uses") == INSTALL_TOOL_ACTION
        and str(step.get("with", {}).get(TOOL_INPUT, "")) == TOOL_NAME
    ]


#: A bare `env.NAME`, the one expression shape this workflow uses.
_ENV_EXPRESSION: typ.Final[re.Pattern[str]] = re.compile(
    r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)


def _environment(scope: object) -> dict[str, object]:
    """Return the `env` mapping *scope* declares, or an empty one."""
    environment = typ.cast("dict[str, object]", scope).get("env")
    return environment if isinstance(environment, dict) else {}


def _resolve_workflow_expression(
    value: str,
    *,
    step: WorkflowStep,
    job: WorkflowJob,
    workflow: Workflow,
) -> str:
    """Resolve a bare `env.NAME` against the scopes GitHub would search.

    Step, then job, then workflow, and no further. Searching every job's
    environment, which this helper used to do, lets a name defined in an
    unrelated job satisfy the version check while `install-tool` receives
    nothing and fails closed on a real run.

    Presence decides, not truth: a declaration at a scope masks the outer
    ones even when its value is blank, and a valueless `NAME:` parses to
    `None` rather than to the empty string.

    Only the one shape the workflow uses is resolved. Anything else is
    returned unchanged, so an unrecognised expression fails the version
    check loudly rather than being quietly accepted.
    """
    match = _ENV_EXPRESSION.fullmatch(value.strip())
    if match is None:
        return value
    name = match.group(1)
    for scope in (step, job, workflow):
        environment = _environment(scope)
        if name in environment:
            resolved = environment[name]
            return "" if resolved is None else str(resolved)
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
                str(step.get("with", {}).get(VERSION_INPUT, "")),
                step=step,
                job=job,
                workflow=ci_document,
            )
            for job, step in _install_tool_steps(ci_document)
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
            pytest.param(
                f"if true; then cargo install {TOOL_NAME}; fi",
                True,
                id="inline-behind-then",
            ),
            pytest.param(
                f"while true; do cargo install {TOOL_NAME}; done",
                True,
                id="inline-behind-do",
            ),
            pytest.param(
                f"if false; then :; else cargo install {TOOL_NAME}; fi",
                True,
                id="inline-behind-else",
            ),
            pytest.param(
                f"until cargo install {TOOL_NAME}; do sleep 1; done",
                True,
                id="inline-behind-until",
            ),
            pytest.param(
                f"echo redo cargo install {TOOL_NAME}",
                False,
                id="a-word-merely-ending-in-a-keyword",
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
            pytest.param(
                f"RUSTUP_TOOLCHAIN=1.95.0 cargo install {TOOL_NAME} --locked",
                True,
                id="behind-an-environment-assignment",
            ),
            pytest.param(
                f"CARGO_NET_OFFLINE=false RUSTFLAGS= cargo install {TOOL_NAME}",
                True,
                id="behind-several-assignments-one-empty",
            ),
            pytest.param(
                f"if true; then RUSTUP_TOOLCHAIN=1.95.0 cargo install {TOOL_NAME}; fi",
                True,
                id="behind-an-assignment-behind-a-keyword",
            ),
            pytest.param(
                f"echo RUSTUP_TOOLCHAIN=1.95.0 cargo install {TOOL_NAME}",
                False,
                id="an-assignment-that-is-only-an-argument",
            ),
            pytest.param(
                f"RUSTUP_TOOLCHAIN=1.95.0 uv tool install {TOOL_NAME}",
                False,
                id="an-assignment-in-front-of-another-installer",
            ),
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
        assert bool(_CARGO_INSTALL.search(script)) is expected, (
            f"{script!r} should {'' if expected else 'not '}be read as a "
            f"source build of {TOOL_NAME}"
        )


class TestVersionResolutionScope:
    """Which `env` an `env.NAME` in an `install-tool` input resolves against.

    GitHub searches the step, then the job, then the workflow, and stops.
    The helper used to search every job's environment, so a name declared
    anywhere in the file satisfied the version check while `install-tool`
    on a real run received an unresolved expression and failed closed.
    """

    @staticmethod
    def _document(
        *,
        job_env: dict[str, object] | None = None,
        step_env: dict[str, object] | None = None,
        workflow_env: dict[str, object] | None = None,
        other_job_env: dict[str, object] | None = None,
    ) -> Workflow:
        """Return a two-job workflow with `env` placed at chosen scopes."""
        step: WorkflowStep = {
            "uses": INSTALL_TOOL_ACTION,
            "with": {TOOL_INPUT: TOOL_NAME, VERSION_INPUT: "${{ env.THE_VERSION }}"},
        }
        if step_env is not None:
            step["env"] = step_env
        installing: WorkflowJob = {"steps": [step]}
        if job_env is not None:
            installing["env"] = job_env
        elsewhere: WorkflowJob = {"steps": []}
        if other_job_env is not None:
            elsewhere["env"] = other_job_env
        document: Workflow = {"jobs": {"installs": installing, "elsewhere": elsewhere}}
        if workflow_env is not None:
            document["env"] = workflow_env
        return document

    def _resolve(self, document: Workflow) -> str:
        """Resolve the version input of the document's install step."""
        job, step = _install_tool_steps(document)[0]
        return _resolve_workflow_expression(
            str(step.get("with", {}).get(VERSION_INPUT, "")),
            step=step,
            job=job,
            workflow=document,
        )

    def test_another_jobs_environment_does_not_resolve_it(self) -> None:
        """A name defined only in an unrelated job leaves the value unresolved.

        This is the mutation that defeated the earlier helper. The value
        must come back as the expression, so that the version check reads
        it as unpinned and fails, exactly as `install-tool` would.
        """
        document = self._document(other_job_env={"THE_VERSION": "0.7.0"})

        assert self._resolve(document) == "${{ env.THE_VERSION }}", (
            "a THE_VERSION declared in another job must not resolve here; "
            "GitHub resolves env.* within the current job"
        )

    @pytest.mark.parametrize(
        "scope",
        ["step_env", "job_env", "workflow_env"],
    )
    def test_the_scopes_github_searches_do_resolve_it(self, scope: str) -> None:
        """Step, job and workflow environments each resolve the name.

        The narrow direction. A helper that only read the job's `env`
        would refuse the workflow-level declaration that GitHub accepts,
        and would fail a workflow that is perfectly correct.
        """
        document = self._document(**{scope: {"THE_VERSION": "0.7.0"}})

        assert self._resolve(document) == "0.7.0", (
            f"a THE_VERSION declared at the {scope} scope must resolve"
        )

    def test_the_nearest_scope_wins(self) -> None:
        """A step declaration masks the job's, and the job's the workflow's."""
        document = self._document(
            step_env={"THE_VERSION": "0.7.0"},
            job_env={"THE_VERSION": "0.6.0"},
            workflow_env={"THE_VERSION": "0.5.0"},
        )

        assert self._resolve(document) == "0.7.0", (
            "the step's declaration is nearest and must win"
        )

    @pytest.mark.parametrize(
        "blank",
        [pytest.param("", id="empty-string"), pytest.param(None, id="valueless")],
    )
    def test_a_blank_declaration_still_masks_the_outer_one(self, blank: object) -> None:
        """A blank `env` entry is a declaration and masks the scope outside it.

        Both spellings are here because they parse differently: `NAME: ""`
        gives the empty string and a valueless `NAME:` gives `None`. A
        reader that tested truth rather than presence would fall through
        to the job's value on both, and one that tested `is not None`
        would fall through on the second.
        """
        document = self._document(
            step_env={"THE_VERSION": blank},
            job_env={"THE_VERSION": "0.7.0"},
        )

        assert self._resolve(document) == "", (
            f"a blank step declaration ({blank!r}) masks the job's 0.7.0"
        )


#: A command boundary the pattern must recognise. The empty string is
#: the start of a line, which the pattern reaches through its
#: `MULTILINE` anchor.
_BOUNDARIES: typ.Final[tuple[str, ...]] = (
    "",
    "true; ",
    "true && ",
    "true || ",
    "if true; then ",
    "while true; do ",
    "until false; do ",
)

#: A word that leaves what follows it as an argument rather than as a
#: command. Prefixing a bare command with one of these must hide it.
_NON_BOUNDARIES: typ.Final[tuple[str, ...]] = (
    "echo ",
    "echo redo ",
    "# ",
    "printf %s ",
)


def _assignments() -> st.SearchStrategy[str]:
    """Return zero or more shell environment assignments, trailing space included."""
    names = st.sampled_from(
        ("RUSTUP_TOOLCHAIN", "CARGO_NET_OFFLINE", "RUSTFLAGS", "_X1")
    )
    values = st.sampled_from(("1.95.0", "false", "", "-Dwarnings"))
    return st.lists(
        st.tuples(names, values).map(lambda pair: f"{pair[0]}={pair[1]}"),
        max_size=3,
    ).map(lambda parts: "".join(f"{part} " for part in parts))


def _options() -> st.SearchStrategy[str]:
    """Return an option sequence `cargo install` accepts before the crate name."""
    return st.lists(
        st.sampled_from(("--locked", "--force", "-q", "--root /tmp/x", "-j 4")),
        max_size=3,
    ).map(lambda parts: "".join(f"{part} " for part in parts))


def _source_builds(
    boundaries: tuple[str, ...] = _BOUNDARIES,
    *,
    crate: str = TOOL_NAME,
) -> st.SearchStrategy[str]:
    """Return commands that really would compile *crate* from source.

    Parameters
    ----------
    boundaries : tuple[str, ...]
        The command boundaries to draw from. The negative properties pass
        the bare form alone, because prefixing a command that carries its
        own separator leaves a genuine command after that separator.
    crate : str
        The crate the generated command installs.
    """
    return st.builds(
        lambda boundary, assignments, selector, options, spelling: (
            f"{boundary}{assignments}cargo{selector} install {options}{spelling}"
        ),
        boundary=st.sampled_from(boundaries),
        assignments=_assignments(),
        selector=st.sampled_from(("", " +1.95.0", " +stable", " +nightly-2026-05-28")),
        options=_options(),
        spelling=st.sampled_from((crate, f"{crate}@0.7.0", f"{crate}@1.0")),
    )


class TestTheSourceBuildMatcherOverGeneratedCommands:
    """The matcher's claim, stated over the space rather than over examples.

    The parametrised cases above pin the requirement at the spellings
    this repository has actually seen. They cannot show that the claim
    holds across the dimensions independently: the pattern composes a
    boundary, any number of environment assignments, an optional
    toolchain selector, an option sequence and a crate spelling, and a
    handful of examples touches only a handful of those combinations. A
    pattern that happened to require an option before the crate name, or
    that allowed assignments only where no selector followed, would pass
    every case above.
    """

    @given(script=_source_builds())
    def test_every_generated_source_build_is_caught(self, script: str) -> None:
        """Each command the strategy builds really would compile the crate."""
        assert _CARGO_INSTALL.search(script) is not None, (
            f"{script!r} compiles {TOOL_NAME} from source and was not caught; "
            "the pattern must hold across each dimension independently, not "
            "only on the combinations written out as cases"
        )

    @given(
        script=_source_builds(("",)),
        prefix=st.sampled_from(_NON_BOUNDARIES),
    )
    def test_a_command_that_is_only_an_argument_is_not_caught(
        self, script: str, prefix: str
    ) -> None:
        """A build quoted as an argument to another command is not a build.

        The narrow direction carries the same weight as the wide one. A
        pattern that fired on the text wherever it appeared would refuse
        a comment recording why the build was removed, and would survive
        its own mutation while discriminating nothing.

        Only the bare form is prefixed. A command drawn with its own
        separator, such as `true; cargo install ...`, still holds a real
        command after that separator, so prefixing it would assert
        something false.
        """
        quoted = f"{prefix}{script}"
        assert _CARGO_INSTALL.search(quoted) is None, (
            f"{quoted!r} passes the command to {prefix.strip()!r} rather than "
            f"running it, so it does not compile {TOOL_NAME}"
        )

    @given(
        script=st.sampled_from(
            (f"{TOOL_NAME}-extras", f"{TOOL_NAME}x", "merman", "some-other-crate")
        ).flatmap(lambda crate: _source_builds(crate=crate))
    )
    def test_another_crate_is_never_caught(self, script: str) -> None:
        """Only this crate's own source build is refused.

        A rule firing on every `cargo install` would stop the repository
        installing anything from source, which is not what the issue this
        branch closes asked for, and a rule matching the crate name as a
        prefix would refuse a differently named crate that merely starts
        with it.
        """
        assert _CARGO_INSTALL.search(script) is None, (
            f"{script!r} does not build {TOOL_NAME} and must not be read as "
            "this crate's source build"
        )
