"""How the workflow-boundary readings behave on inputs chosen for a case.

Separated from `test_main_owned_coverage`, whose subject is this repository's
own workflows. Every assertion there quantifies over what it finds, so a
reading that is wrong would have to be wrong about a file this repository
happens to contain before anything fails. These drive the readings directly,
on documents written for the purpose: the `on:` key under both spellings
PyYAML produces, local `uses:` references in every spelling that resolves
under this repository's workflow directory, a digest offender under each file
extension, and generated workflow graphs with cycles, which this
repository's shallow forest of callers cannot exercise.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from .workflow_boundary import (
    CODESCENE_CREDENTIAL,
    DIGEST_VARIABLE,
    LOCAL_WORKFLOW_PATH,
    SELF_PREFIXES,
    _called_workflow,
    _callees,
    coverage_steps,
    digest_offenders,
    digest_refreshers,
    effective_text,
    names_the_codescene_host,
    platforms,
    pull_request_reachable,
)
from .workflow_triggers import pushes_to_main, starts_on_pull_request

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from .test_coverage_timeout_tiers import WorkflowDocument


class TestTheTriggerReaderSeesBothKeys:
    """Drive the reader directly, on documents chosen rather than found.

    Parametrised over this repository's compliant workflows the reader would
    pass whether or not it understood the boolean key, because a reader that
    resolves nothing agrees with a repository that calls CodeScene nowhere.
    """

    @pytest.mark.parametrize(
        ("reader", "document", "expected"),
        [
            *(
                pytest.param(starts_on_pull_request, document, expected, id=case)
                for document, expected, case in (
                    ({True: {"pull_request": None}}, True, "pr-boolean-key"),
                    ({"on": {"pull_request": None}}, True, "pr-string-key"),
                    ({True: ["pull_request"]}, True, "pr-boolean-key-list"),
                    ({True: "pull_request"}, True, "pr-boolean-key-string"),
                    ({"on": ["push", "pull_request"]}, True, "pr-string-key-list"),
                    ({"on": "pull_request"}, True, "pr-string-key-string"),
                    ({True: {"push": {"branches": ["main"]}}}, False, "pr-push"),
                    ({}, False, "pr-no-triggers"),
                )
            ),
            *(
                pytest.param(pushes_to_main, document, expected, id=case)
                for document, expected, case in (
                    ({True: {"push": {"branches": ["main"]}}}, True, "push-main"),
                    ({True: {"push": None}}, True, "push-unfiltered"),
                    ({True: {"push": {"branches": ["dev"]}}}, False, "push-other"),
                    ({True: {"workflow_dispatch": None}}, False, "push-dispatch"),
                    ({True: ["push"]}, True, "push-list"),
                    ({True: {"push": {"branches": "main"}}}, True, "push-string"),
                    ({True: {"push": {"branches": ["**"]}}}, True, "push-glob"),
                    (
                        {True: {"push": {"branches": ["**", "!main"]}}},
                        False,
                        "push-negated",
                    ),
                    (
                        {True: {"push": {"branches": ["!main", "ma*"]}}},
                        True,
                        "push-negation-overridden",
                    ),
                    (
                        {True: {"push": {"branches-ignore": ["main"]}}},
                        False,
                        "push-ignored",
                    ),
                    (
                        {True: {"push": {"branches-ignore": ["dev"]}}},
                        True,
                        "push-other-ignored",
                    ),
                    ({True: {"push": {"tags": ["v*"]}}}, False, "push-tags-only"),
                    (
                        {True: {"push": {"tags": ["v*"], "branches": ["main"]}}},
                        True,
                        "push-tags-and-main",
                    ),
                    ({True: {"push": {"paths": ["src/**"]}}}, True, "push-paths"),
                )
            ),
        ],
    )
    def test_a_trigger_is_read_under_either_key(
        self,
        reader: cabc.Callable[[dict[typ.Any, typ.Any]], bool],
        document: dict[typ.Any, typ.Any],
        *,
        expected: bool,
    ) -> None:
        """Both readers see every spelling, under the string key and ``True``."""
        assert reader(document) is expected, (
            f"{reader.__name__}({document!r}) should be {expected}"
        )


class TestTheRunnerIsReadFromTheNamedDimension:
    """``runs-on: ${{ matrix.os }}`` names one dimension of the matrix."""

    def test_only_the_named_dimension_is_a_platform(self) -> None:
        """A Python version beside the runner dimension is not a platform.

        Read as one, it becomes a platform the publisher would have to
        ratchet, and no runner has that name. An ``include`` entry that adds a
        runner is part of the dimension.
        """
        document = {
            "jobs": {
                "a": {
                    "runs-on": "${{ matrix.os }}",
                    "strategy": {
                        "matrix": {
                            "os": ["ubuntu-latest", "windows-latest"],
                            "python-version": ["3.12", "3.13"],
                            "include": [{"os": "macos-latest"}],
                        }
                    },
                }
            }
        }
        found = platforms(document, "a")
        assert found == {"ubuntu", "windows", "macos"}, found


class TestTheSelfReferenceReaderMatchesByShape:
    """Every spelling named literally, so narrowing the reader fails.

    Parametrising these over ``SELF_PREFIXES`` would make a reader that forgot
    ``$/`` pass with fewer cases rather than fail, which is how a rule over a
    filtered list is satisfied by emptying it. A reference is local when it
    resolves under the workflow directory, whatever it is prefixed with and
    whatever ``@ref`` it carries: refusing a spelling GitHub might reject
    takes its target out of the boundary in silence, while recognizing it
    only adds prohibitions.
    """

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/workflows/a.yml", "a.yml", id="relative"),
            pytest.param("$/.github/workflows/a.yml", "a.yml", id="self-repository"),
            pytest.param("./.github/workflows/a.yml@main", "a.yml", id="relative-ref"),
            pytest.param(
                "$/.github/workflows/a.yml@main", "a.yml", id="self-ref-suffix"
            ),
            pytest.param(".github/workflows/a.yml", "a.yml", id="unprefixed"),
            pytest.param(
                "./.github/actions/../workflows/a.yml", "a.yml", id="non-normal"
            ),
            pytest.param("other/repo/.github/workflows/a.yml@v1", None, id="foreign"),
            pytest.param("./.github/workflows-old/a.yml", None, id="sibling-directory"),
            pytest.param("./.github/actions/a", None, id="an-action"),
            pytest.param("", None, id="a-step-job"),
        ],
    )
    def test_a_called_workflow_is_named(self, uses: str, expected: str | None) -> None:
        """What a job delegates to, or nothing when the reference is not local."""
        assert _called_workflow({"uses": uses}) == expected, uses

    @pytest.mark.parametrize(
        ("uses", "recognised"),
        [
            pytest.param("./.github/actions/generate-coverage", True, id="relative"),
            pytest.param("$/.github/actions/generate-coverage", True, id="self-repo"),
            pytest.param(
                "$/.github/actions/generate-coverage@main", True, id="self-ref-suffix"
            ),
            pytest.param(".github/actions/generate-coverage", True, id="unprefixed"),
            pytest.param("./.github/actions/setup-rust", False, id="another-action"),
            pytest.param(
                "./.github/actions/generate-coverage-next", False, id="longer-name"
            ),
        ],
    )
    def test_the_coverage_action_is_recognised(
        self,
        uses: str,
        *,
        recognised: bool,
    ) -> None:
        """A lane that switched syntax must not escape the coverage rules."""
        document = {"jobs": {"a": {"steps": [{"uses": uses}]}}}
        assert bool(coverage_steps(document)) is recognised, uses

    def test_reachability_follows_a_called_workflow(self) -> None:
        """A caller a pull request starts drags its callee into the boundary."""
        documents: dict[str, WorkflowDocument] = {
            "caller.yml": typ.cast(
                "WorkflowDocument",
                {
                    True: {"pull_request": None},
                    "jobs": {"call": {"uses": f"./{LOCAL_WORKFLOW_PATH}callee.yml"}},
                },
            ),
            "callee.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_call": None}, "jobs": {}}
            ),
            "unrelated.yml": typ.cast(
                "WorkflowDocument", {True: {"workflow_dispatch": None}, "jobs": {}}
            ),
        }
        reached = pull_request_reachable(documents)
        assert reached == {"caller.yml", "callee.yml"}, reached


#: One generated workflow: whether a pull request starts it, and which of the
#: graph's workflows its one job delegates to, by index.
WorkflowShape = tuple[bool, int | None]


def _graph(
    shapes: list[WorkflowShape], prefix: str = "./"
) -> dict[str, WorkflowDocument]:
    """Build a workflow graph from generated shapes.

    Each workflow gets at most one calling job, which is enough to express
    any reachability the real reader can meet: a caller with several jobs is
    the same relation with more edges. The self-repository prefix is a
    parameter, so the invariants hold for both spellings rather than for the
    one this repository happens to use today.
    """
    names = [f"w{index}.yml" for index in range(len(shapes))]
    documents: dict[str, WorkflowDocument] = {}
    for name, (starts, callee) in zip(names, shapes, strict=True):
        jobs: dict[str, typ.Any] = {}
        if callee is not None:
            jobs["call"] = {"uses": f"{prefix}{LOCAL_WORKFLOW_PATH}{names[callee]}"}
        documents[name] = typ.cast(
            "WorkflowDocument",
            {True: {"pull_request": None} if starts else {"push": None}, "jobs": jobs},
        )
    return documents


#: Graphs of up to six workflows, each with an optional edge to any of them.
#: Self-edges and cycles are generated deliberately: a traversal that marks
#: after visiting rather than before would not terminate on them, and this
#: repository's own workflows contain no cycle to find that with.
WORKFLOW_GRAPHS: typ.Final = st.integers(min_value=1, max_value=6).flatmap(
    lambda size: st.lists(
        st.tuples(
            st.booleans(),
            st.one_of(st.none(), st.integers(min_value=0, max_value=size - 1)),
        ),
        min_size=size,
        max_size=size,
    )
)


def _walk(
    shapes: list[WorkflowShape], prefix: str
) -> tuple[dict[str, WorkflowDocument], set[str]]:
    """Return a generated graph and what the walk reaches in it."""
    documents = _graph(shapes, prefix)
    return documents, pull_request_reachable(documents)


def over_generated_graphs(test: cabc.Callable[..., None]) -> cabc.Callable[..., None]:
    """Run *test* over every generated graph and both self-repository prefixes.

    Every invariant below holds for any graph and either prefix, so naming
    the two decorators once keeps each case to its assertion.
    """
    return pytest.mark.parametrize("prefix", SELF_PREFIXES)(
        given(shapes=WORKFLOW_GRAPHS)(test)
    )


class TestReachabilityOverGeneratedGraphs:
    """The traversal's three invariants, on graphs chosen by Hypothesis.

    The repository's own workflows form a shallow forest with no cycle and
    one level of delegation, so the assertions above exercise the walk on a
    single shape. These state what the walk must be on any shape, which is
    what a contract quantifying over "every reachable workflow" relies on.
    """

    @over_generated_graphs
    def test_every_pull_request_trigger_is_reached(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """A workflow a pull request starts is always inside the boundary."""
        documents, reached = _walk(shapes, prefix)
        started = {
            name
            for name, document in documents.items()
            if starts_on_pull_request(document)
        }
        assert started <= reached, (
            f"pull-request started but unreached: {sorted(started - reached)}"
        )

    @over_generated_graphs
    def test_the_boundary_is_closed_under_delegation(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Nothing a reached workflow calls is left outside.

        This is the half a CodeScene call moved one file away would exploit.
        """
        documents, reached = _walk(shapes, prefix)
        for name in reached:
            escaped = _callees(documents, name) - reached
            assert not escaped, f"{name} calls unreached workflows: {sorted(escaped)}"

    @over_generated_graphs
    def test_nothing_is_reached_without_a_reason(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Every member is pull-request started or called by another member.

        Without this half the walk could satisfy the other two by returning
        every workflow in the repository, which would make the boundary
        assertions fail on files no pull request can run.
        """
        documents, reached = _walk(shapes, prefix)
        callers = {callee for name in reached for callee in _callees(documents, name)}
        for name in reached:
            assert starts_on_pull_request(documents[name]) or name in callers, (
                f"{name} is reached but nothing starts or calls it"
            )


#: The two spellings GitHub reads a workflow from.
EXTENSIONS: typ.Final[tuple[str, ...]] = (".yml", ".yaml")


def _plant(directory: Path, name: str, body: str) -> str:
    """Write one workflow into *directory* and return its file name."""
    (directory / name).write_text(body, encoding="utf-8")
    return name


class TestTheDigestScanFindsWhatIsPlanted:
    """Drive the scan on directories built to contain an offender.

    Over this repository's compliant workflows it passes whether or not it
    reads both file extensions, so the reading is exercised here. GitHub runs
    a workflow spelled either way.
    """

    @pytest.mark.parametrize("suffix", EXTENSIONS)
    def test_the_dead_variable_is_found(self, tmp_path: Path, suffix: str) -> None:
        """A workflow reading the variable is named, whatever it is spelled."""
        name = _plant(
            tmp_path,
            f"refresh{suffix}",
            f"on:\n  workflow_dispatch:\njobs:\n  a:\n    env:\n"
            f"      X: ${{{{ vars.{DIGEST_VARIABLE} }}}}\n",
        )
        found = digest_offenders(tmp_path)
        assert found == {name: [DIGEST_VARIABLE]}, (
            f"planting {name} should be found as {DIGEST_VARIABLE}; read {found}"
        )

    @pytest.mark.parametrize("suffix", EXTENSIONS)
    def test_the_refresher_is_found(self, tmp_path: Path, suffix: str) -> None:
        """The refresher's only output was that variable (YAGNI, 2026-09-18)."""
        name = _plant(tmp_path, f"get-codescene-sha{suffix}", "{}")
        found = digest_refreshers(tmp_path)
        assert found == [name], f"planting {name} should be found; read {found}"


class TestTheEffectiveTextReader:
    """What a workflow can act on, and what it merely says.

    Every host and credential assertion in the contract quantifies over this
    reading. Against this repository's compliant workflows it would pass
    whether or not it read `run:` bodies, and whether or not it folded case,
    because none of them names the service at all. These drive it on
    workflows written to name it.
    """

    @pytest.mark.parametrize(
        ("host", "case"),
        [
            pytest.param("codescene.io", "lower", id="lower-case"),
            pytest.param("CodeScene.IO", "mixed", id="mixed-case"),
            pytest.param("CODESCENE.IO", "upper", id="upper-case"),
        ],
    )
    def test_the_host_is_found_whatever_its_case(self, host: str, case: str) -> None:
        """A DNS name is case-insensitive, so the comparison must be.

        Without the fold, a lane reaches the service by capitalising it.
        """
        document = {
            "jobs": {"a": {"steps": [{"run": f"curl -sf https://api.{host}/v2"}]}}
        }
        assert names_the_codescene_host(document), (
            f"the {case}-case host should be recognised; the fold is in the "
            "reader so that removing it fails here"
        )

    def test_a_run_body_is_read(self) -> None:
        """The `run:` script is where a call hides from a shallower reading."""
        document = {"jobs": {"a": {"steps": [{"run": "curl https://codescene.io"}]}}}
        text = effective_text(document)
        assert "codescene.io" in text, text

    def test_an_environment_key_is_read(self) -> None:
        """A credential arrives as a key, with an expression for its value."""
        document = {
            "jobs": {
                "a": {
                    "steps": [
                        {
                            "env": {CODESCENE_CREDENTIAL: "${{ secrets.X }}"},
                            "run": "true",
                        }
                    ]
                }
            }
        }
        text = effective_text(document)
        assert CODESCENE_CREDENTIAL in text, text

    def test_a_workflow_level_environment_is_read(self) -> None:
        """The outermost scope reaches every job in the file."""
        document = {"env": {CODESCENE_CREDENTIAL: "${{ secrets.X }}"}, "jobs": {}}
        text = effective_text(document)
        assert CODESCENE_CREDENTIAL in text, text

    def test_a_boolean_or_an_empty_value_contributes_no_text(self) -> None:
        """Neither is text a step can act on, and both are everywhere.

        YAML spells an empty value ``None`` and resolves bare ``true`` to a
        boolean, so a reader that stringified them would scatter the literals
        ``None`` and ``True`` through the scanned text. Every clause over
        this reading is a substring search, and a marker that contained
        either word would then match a workflow that does nothing of the
        kind. The same holds for a key: an unquoted ``on:`` is the boolean
        ``True``, and the whole document is walked.
        """
        document = {
            True: {"pull_request": None},
            "jobs": {
                "a": {
                    "steps": [
                        {"run": "echo hi", "continue-on-error": True},
                        {"uses": None},
                    ]
                }
            },
        }
        text = effective_text(document)

        assert "echo hi" in text, text
        assert "True" not in text, text
        assert "None" not in text, text

    def test_a_comment_is_not_read(self) -> None:
        """A comment contacts nothing, and the parse has already dropped it.

        This is the half that made the reading necessary: explaining in prose
        why a lane must not name the credential made the lane name it.
        """
        document = yaml.safe_load(
            "# CS_ACCESS_TOKEN must never appear here\n"
            "on:\n  pull_request:\njobs:\n  a:\n    steps:\n"
            "      - run: 'true'\n"
        )
        text = effective_text(document)
        assert CODESCENE_CREDENTIAL not in text, text
