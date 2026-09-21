"""How the workflow-boundary readings behave on inputs chosen for a case.

Separated from `test_main_owned_coverage`, whose subject is this repository's
own workflows. Every assertion there quantifies over what it finds, so a
reading that is wrong would have to be wrong about a file this repository
happens to contain before anything fails. These drive the readings directly,
on documents written for the purpose: the `on:` key under both spellings
PyYAML produces, both self-repository `uses:` prefixes named literally, a
`$/` reference carrying the `@ref` GitHub forbids, a digest offender under
each file extension, and generated workflow graphs with cycles, which this
repository's shallow forest of callers cannot exercise.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from .test_main_owned_coverage import (
    DIGEST_VARIABLE,
    LOCAL_WORKFLOW_PATH,
    SELF_PREFIXES,
    WorkflowDocument,
    _called_workflow,
    _callees,
    coverage_steps,
    digest_offenders,
    digest_refreshers,
    pull_request_reachable,
    pushes_to_main,
    starts_on_pull_request,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestTheTriggerReaderSeesBothKeys:
    """Drive the reader directly, on documents chosen rather than found.

    Parametrised over this repository's compliant workflows the reader would
    pass whether or not it understood the boolean key, because a reader that
    resolves nothing agrees with a repository that calls CodeScene nowhere.
    """

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param({True: {"pull_request": None}}, True, id="boolean-key"),
            pytest.param({"on": {"pull_request": None}}, True, id="string-key"),
            pytest.param({True: ["pull_request"]}, True, id="boolean-key-list"),
            pytest.param({True: "pull_request"}, True, id="boolean-key-string"),
            pytest.param({True: {"push": {"branches": ["main"]}}}, False, id="push"),
            pytest.param({}, False, id="no-triggers"),
        ],
    )
    def test_pull_request_detection(
        self,
        document: dict[typ.Any, typ.Any],
        *,
        expected: bool,
    ) -> None:
        """A pull-request trigger is seen under either key and every spelling."""
        assert starts_on_pull_request(document) is expected, (
            f"{document!r} should read as pull-request started={expected}"
        )

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param({True: {"push": {"branches": ["main"]}}}, True, id="main"),
            pytest.param({True: {"push": None}}, True, id="unfiltered"),
            pytest.param({True: {"push": {"branches": ["dev"]}}}, False, id="other"),
            pytest.param({True: {"workflow_dispatch": None}}, False, id="dispatch"),
        ],
    )
    def test_main_push_detection(
        self,
        document: dict[typ.Any, typ.Any],
        *,
        expected: bool,
    ) -> None:
        """A push to main is recognised under a filter and without one."""
        assert pushes_to_main(document) is expected, (
            f"{document!r} should read as main-push started={expected}"
        )

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/workflows/a.yml", "a.yml", id="relative"),
            pytest.param("$/.github/workflows/a.yml", "a.yml", id="self-repository"),
            pytest.param("./.github/workflows/a.yml@main", "a.yml", id="relative-ref"),
            pytest.param("$/.github/workflows/a.yml@main", None, id="self-ref-suffix"),
            pytest.param("other/repo/.github/workflows/a.yml@v1", None, id="foreign"),
            pytest.param("./.github/actions/a", None, id="an-action"),
            pytest.param("", None, id="a-step-job"),
        ],
    )
    def test_the_self_reference_reader_knows_both_spellings(
        self, uses: str, expected: str | None
    ) -> None:
        """Name the two prefixes literally, so narrowing the constant fails.

        Parametrising this over ``SELF_PREFIXES`` would make a reader that
        forgot ``$/`` pass with three fewer cases rather than fail, which is
        how a filtered list satisfies a rule by becoming empty.
        """
        assert _called_workflow({"uses": uses}) == expected, uses

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/actions/generate-coverage", True, id="relative"),
            pytest.param("$/.github/actions/generate-coverage", True, id="self-repo"),
            pytest.param(
                "$/.github/actions/generate-coverage@main", False, id="self-ref-suffix"
            ),
            pytest.param("./.github/actions/setup-rust", False, id="another-action"),
        ],
    )
    def test_the_coverage_action_is_recognised_in_both_spellings(
        self,
        uses: str,
        *,
        expected: bool,
    ) -> None:
        """A lane that switched syntax must not escape the coverage rules.

        A `$/` reference carrying an `@ref` is invalid to GitHub, so it is
        rejected rather than stripped and accepted.
        """
        document = {"jobs": {"a": {"steps": [{"uses": uses}]}}}
        assert bool(coverage_steps(document)) is expected, uses

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


class TestReachabilityOverGeneratedGraphs:
    """The traversal's three invariants, on graphs chosen by Hypothesis.

    The repository's own workflows form a shallow forest with no cycle and
    one level of delegation, so the assertions above exercise the walk on a
    single shape. These state what the walk must be on any shape, which is
    what a contract quantifying over "every reachable workflow" relies on.
    """

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_every_pull_request_trigger_is_reached(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """A workflow a pull request starts is always inside the boundary."""
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        started = {
            name
            for name, document in documents.items()
            if starts_on_pull_request(document)
        }
        assert started <= reached, (
            f"pull-request started but unreached: {sorted(started - reached)}"
        )

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_the_boundary_is_closed_under_delegation(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Nothing a reached workflow calls is left outside.

        This is the half a CodeScene call moved one file away would exploit.
        """
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        for name in reached:
            escaped = _callees(documents, name) - reached
            assert not escaped, f"{name} calls unreached workflows: {sorted(escaped)}"

    @pytest.mark.parametrize("prefix", SELF_PREFIXES)
    @given(shapes=WORKFLOW_GRAPHS)
    def test_nothing_is_reached_without_a_reason(
        self, shapes: list[WorkflowShape], prefix: str
    ) -> None:
        """Every member is pull-request started or called by another member.

        Without this half the walk could satisfy the other two by returning
        every workflow in the repository, which would make the boundary
        assertions fail on files no pull request can run.
        """
        documents = _graph(shapes, prefix)
        reached = pull_request_reachable(documents)
        callers = {callee for name in reached for callee in _callees(documents, name)}
        for name in reached:
            assert starts_on_pull_request(documents[name]) or name in callers, (
                f"{name} is reached but nothing starts or calls it"
            )


class TestTheDigestScanFindsWhatIsPlanted:
    """Drive the scan on directories built to contain an offender.

    Over this repository's compliant workflows it passes whether or not it
    reads both file extensions, so the reading is exercised here. GitHub runs
    a workflow spelled either way.
    """

    @pytest.mark.parametrize("suffix", [".yml", ".yaml"])
    def test_an_offender_is_found_under_either_extension(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """Drive the scan on a directory built to contain one.

        Over this repository's compliant workflows the scan passes whether or
        not it reads both extensions, so the reading is exercised here. GitHub
        runs a workflow spelled either way.
        """
        (tmp_path / f"refresh{suffix}").write_text(
            f"on:\n  workflow_dispatch:\njobs:\n  a:\n    env:\n"
            f"      X: ${{{{ vars.{DIGEST_VARIABLE} }}}}\n",
            encoding="utf-8",
        )
        assert digest_offenders(tmp_path) == {f"refresh{suffix}": [DIGEST_VARIABLE]}

    @pytest.mark.parametrize("suffix", [".yml", ".yaml"])
    def test_a_refresher_is_found_under_either_extension(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """The refresher's only output was that variable (YAGNI, 2026-09-18)."""
        (tmp_path / f"get-codescene-sha{suffix}").write_text("{}", encoding="utf-8")
        assert digest_refreshers(tmp_path) == [f"get-codescene-sha{suffix}"]
