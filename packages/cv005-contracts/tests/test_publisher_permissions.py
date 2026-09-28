"""Cases for where the publisher's jobs take their token scope from.

A job's own `permissions` replaces the workflow's, so the rule is held per
job: each declares its scope, or the workflow grants nothing.
"""

from __future__ import annotations

import pytest
from contract_fixtures import mutate
from cv005_contracts.loading import load_workflow
from cv005_contracts.publisher_rules import permissions_violations

#: The fixture publisher's one job-level declaration.
JOB_GRANT = "    permissions:\n      contents: read\n"


@pytest.mark.parametrize(
    "grant",
    [
        "",
        "permissions: write-all\n",
        "permissions:\n  id-token: write\n  contents: read\n",
    ],
)
def test_a_job_left_to_the_default_or_a_write_grant_is_refused(grant: str) -> None:
    """A job declaring nothing runs with the workflow's scope or the default."""
    texts = mutate("coverage-main.yml", "permissions: {}\n", grant)
    text = texts["coverage-main.yml"].replace(JOB_GRANT, "")
    assert JOB_GRANT not in text
    found = permissions_violations(load_workflow(text))
    assert found, found


@pytest.mark.parametrize(
    "grant",
    [
        "permissions: {}\n",
        "permissions: read-all\n",
        "permissions:\n  contents: read\n",
    ],
)
def test_a_job_under_a_read_only_workflow_grant_is_accepted(grant: str) -> None:
    """Cuprum's shape: the workflow grants `contents: read` to every job."""
    texts = mutate("coverage-main.yml", "permissions: {}\n", grant)
    text = texts["coverage-main.yml"].replace(JOB_GRANT, "")
    assert JOB_GRANT not in text
    found = permissions_violations(load_workflow(text))
    assert not found, found


@pytest.mark.parametrize("grant", ["", "permissions: write-all\n"])
def test_every_job_declaring_its_own_scope_is_accepted(grant: str) -> None:
    """A job's own `permissions` replaces the workflow's, whatever it is."""
    texts = mutate("coverage-main.yml", "permissions: {}\n", grant)
    found = permissions_violations(load_workflow(texts["coverage-main.yml"]))
    assert not found, found


def test_a_job_under_no_workflow_grant_is_named_as_holding_the_default() -> None:
    """With no `permissions` anywhere the job holds the repository default."""
    texts = mutate("coverage-main.yml", "permissions: {}\n", "")
    text = texts["coverage-main.yml"].replace(JOB_GRANT, "")
    found = permissions_violations(load_workflow(text))
    assert found == [
        "job coverage-upload declares no permissions and runs with the default token"
    ], found
