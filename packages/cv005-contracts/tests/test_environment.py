"""Prove the `codescene` environment sits on the uploading job alone.

Each test mutates a copy of this repository's workflows the way a later edit
could, and asserts the clause meant to catch it does. The check step, the ref
guard and `access-token:` stay held by the repository contract.
"""

from __future__ import annotations

import typing as typ

import pytest
from contract_fixtures import REPOSITORY, parse_tree, tree
from cv005_contracts.environment import (
    MISSING,
    REACHABLE,
    STRAY,
    UNRESOLVED,
    environment_violations,
)
from cv005_contracts.loading import Document, load_workflow
from cv005_contracts.publisher import UPLOAD_ACTION
from cv005_contracts.reading import jobs

PUBLISHER: typ.Final[str] = "coverage-main.yml"
LANE: typ.Final[str] = "ci.yml"


@pytest.fixture
def documents() -> dict[str, Document]:
    """Return a private parse of the compliant fixture tree to mutate.

    Returns
    -------
    dict[str, Document]
        The parsed workflows, fresh for this test alone.

    """
    return parse_tree(tree())


def _first_job(documents: dict[str, Document], name: str) -> dict[str, object]:
    """Return one workflow's first job, for mutation in place.

    Returns
    -------
    dict[str, object]
        The job mapping.

    """
    return next(iter(jobs(documents[name]).values()))


def _reports(documents: dict[str, Document], fragment: str) -> None:
    """Fail unless the rule reports a violation containing `fragment`."""
    found = environment_violations(documents, REPOSITORY)
    assert any(fragment in problem for problem in found), (
        f"expected a violation naming {fragment!r}, got {found}"
    )


def test_repository_places_the_environment(documents: dict[str, Document]) -> None:
    """The publisher declares the environment and nothing else does."""
    found = environment_violations(documents, REPOSITORY)
    assert not found, f"expected no violations, got {found}"


def test_publisher_cannot_drop_the_environment(documents: dict[str, Document]) -> None:
    """Without it the moved token never reaches the upload, which then skips."""
    del _first_job(documents, PUBLISHER)["environment"]
    _reports(documents, MISSING)


def test_publisher_cannot_name_another_environment(
    documents: dict[str, Document],
) -> None:
    """Another environment holds no CodeScene token."""
    _first_job(documents, PUBLISHER)["environment"] = "production"
    _reports(documents, MISSING)


def test_mapping_form_is_accepted(documents: dict[str, Document]) -> None:
    """`{name: codescene}` is the same declaration as the bare string."""
    _first_job(documents, PUBLISHER)["environment"] = {"name": "codescene"}
    found = environment_violations(documents, REPOSITORY)
    assert not found, f"the mapping form must be accepted, got {found}"


@pytest.mark.parametrize("name", ["codescene", "CodeScene"])
def test_no_other_job_may_declare_it(documents: dict[str, Document], name: str) -> None:
    """A second holder of the token widens what can read it, in any case."""
    jobs(documents[PUBLISHER])["other"] = {
        "runs-on": "ubuntu-latest",
        "environment": name,
        "steps": [{"run": "true"}],
    }
    _reports(documents, f"{PUBLISHER}:other {STRAY}")


def test_a_look_alike_action_is_not_the_uploader(
    documents: dict[str, Document],
) -> None:
    """Only the shared uploader's exact path earns the environment."""
    jobs(documents[PUBLISHER])["other"] = {
        "runs-on": "ubuntu-latest",
        "environment": "codescene",
        "steps": [{"uses": f"{UPLOAD_ACTION}-check@v1"}],
    }
    _reports(documents, f"{PUBLISHER}:other {STRAY}")


def test_an_expression_named_environment_is_refused(
    documents: dict[str, Document],
) -> None:
    """A computed name may resolve to `codescene`, so it cannot be placed."""
    _first_job(documents, LANE)["environment"] = {"name": "${{ 'codescene' }}"}
    _reports(documents, f"{LANE}:{next(iter(jobs(documents[LANE])))} {UNRESOLVED}")


@pytest.mark.parametrize("name", ["codescene", "CodeScene"])
def test_no_pull_request_job_may_declare_it(
    documents: dict[str, Document], name: str
) -> None:
    """A pull request's own code must never be able to request the token."""
    _first_job(documents, LANE)["environment"] = {"name": name}
    _reports(documents, REACHABLE)


def test_an_empty_reading_is_refused(documents: dict[str, Document]) -> None:
    """With no uploader left the rule says so rather than passing."""
    job = _first_job(documents, PUBLISHER)
    job["steps"] = [
        step
        for step in typ.cast("list[dict[str, object]]", job["steps"])
        if UPLOAD_ACTION not in str(step.get("uses", ""))
    ]
    _reports(documents, "no workflow job invokes")


#: A pull-request lane that calls a local reusable workflow.
CALLER: typ.Final[str] = (
    "on: [pull_request]\njobs:\n  call:\n"
    "    uses: {prefix}.github/workflows/deploy.yml\n"
)

#: A reusable workflow whose one job declares the environment.
DEPLOY: typ.Final[str] = (
    "on: workflow_call\njobs:\n  deploy:\n    runs-on: x\n"
    "    environment: codescene\n    steps:\n      - run: 'true'\n"
)


@pytest.mark.parametrize("prefix", ["./", "$/"])
def test_a_called_workflow_may_not_declare_it(
    documents: dict[str, Document], prefix: str
) -> None:
    """A workflow a pull request calls runs with the caller's event."""
    documents["caller.yml"] = load_workflow(CALLER.format(prefix=prefix))
    documents["deploy.yml"] = load_workflow(DEPLOY)
    _reports(documents, REACHABLE)


def test_a_workflow_run_chain_may_not_declare_it(
    documents: dict[str, Document],
) -> None:
    """A `workflow_run` workflow runs with secrets after a pull request's run."""
    chained = DEPLOY.replace(
        "on: workflow_call", "on:\n  workflow_run:\n    workflows: [CI]"
    )
    documents["chained.yml"] = load_workflow(chained)
    _reports(documents, REACHABLE)


#: Environment names that are not `codescene`, including ones that contain or
#: extend it, since a rule reading a substring or a prefix would hold them too.
OTHER_ENVIRONMENTS: typ.Final[list[str]] = [
    "production",
    "release",
    "codescene-staging",
    "not-codescene",
    "codescenes",
]


@pytest.mark.parametrize("name", OTHER_ENVIRONMENTS)
def test_other_environments_pass_wherever_they_sit(
    documents: dict[str, Document], name: str
) -> None:
    """The rule holds `codescene` alone: another environment is no finding.

    It sits on a pull-request job, on a job of the publisher that uploads
    nothing, and on a job of a workflow a pull request calls, the three
    places `codescene` itself would be refused.
    """
    _first_job(documents, LANE)["environment"] = {"name": name}
    jobs(documents[PUBLISHER])["other"] = {
        "runs-on": "ubuntu-latest",
        "environment": name,
        "steps": [{"run": "true"}],
    }
    documents["caller.yml"] = load_workflow(CALLER.format(prefix="./"))
    documents["deploy.yml"] = load_workflow(
        DEPLOY.replace("environment: codescene", f"environment: {name}")
    )
    found = environment_violations(documents, REPOSITORY)
    assert not found, f"{name!r} must not be held to the codescene rules: {found}"


def test_a_computed_url_is_not_a_computed_name(
    documents: dict[str, Document],
) -> None:
    """Only the `name` decides placement; an expression in `url` is accepted."""
    _first_job(documents, PUBLISHER)["environment"] = {
        "name": "codescene",
        "url": "${{ steps.deploy.outputs.url }}",
    }
    found = environment_violations(documents, REPOSITORY)
    assert not found, f"a computed url must be accepted, got {found}"


def test_a_computed_url_on_another_environment_is_accepted(
    documents: dict[str, Document],
) -> None:
    """The same holds where the environment is not `codescene`."""
    _first_job(documents, LANE)["environment"] = {
        "name": "preview",
        "url": "${{ steps.deploy.outputs.url }}",
    }
    found = environment_violations(documents, REPOSITORY)
    assert not found, f"a computed url must be accepted, got {found}"
