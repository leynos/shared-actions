"""The pull-request clauses, driven on workflows written to break them.

This repository's workflows break none of these clauses, so every case in
the contract passes whether or not a clause works. These build the
offending shapes and feed them to the same functions the contract runs.

The first is the probe that found the closure hole (episodic, abd47e02): a
file declaring only ``workflow_call``, called from a pull-request job with
``secrets: inherit``, curling the CodeScene project API with the inherited
credential. A clause enumerating only the workflows a pull request starts
directly passes over it.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from .pull_request_boundary import (
    credential_holders,
    host_namers,
    secret_inheritors,
    service_callers,
)
from .workflow_boundary import CODESCENE_CREDENTIAL, LOCAL_WORKFLOW_PATH

if typ.TYPE_CHECKING:
    from .test_coverage_timeout_tiers import WorkflowDocument

#: A job that curls the project API with the credential in a ``run:`` body.
CURLING_JOB: typ.Final[dict[str, typ.Any]] = {
    "runs-on": "ubuntu-latest",
    "steps": [
        {
            "run": (
                "curl -sf -H 'Authorization: Bearer "
                f"${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}' "
                "https://api.codescene.io/v2/projects"
            )
        }
    ],
}


def _probe(uses: str) -> dict[str, WorkflowDocument]:
    """Build the caller and callee pair, the callee named by *uses*."""
    return typ.cast(
        "dict[str, WorkflowDocument]",
        {
            "ci.yml": {
                True: {"pull_request": None},
                "jobs": {"call": {"uses": uses, "secrets": "inherit"}},
            },
            "callee.yml": {
                True: {"workflow_call": None},
                "jobs": {"curl": CURLING_JOB},
            },
        },
    )


class TestTheClosureProbe:
    """Every pull-request clause reaches the called file, in every spelling."""

    @pytest.mark.parametrize(
        "uses",
        [
            pytest.param(f"./{LOCAL_WORKFLOW_PATH}callee.yml", id="relative"),
            pytest.param(f"$/{LOCAL_WORKFLOW_PATH}callee.yml", id="self-repository"),
            pytest.param(f"$/{LOCAL_WORKFLOW_PATH}callee.yml@main", id="with-ref"),
            pytest.param(f"{LOCAL_WORKFLOW_PATH}callee.yml", id="unprefixed"),
        ],
    )
    def test_the_callee_is_named_by_the_credential_and_host_clauses(
        self, uses: str
    ) -> None:
        """The caller names neither; only the closure brings the callee in."""
        documents = _probe(uses)
        holders = credential_holders(documents)
        namers = host_namers(documents)

        assert holders == ["callee.yml"], holders
        assert namers == ["callee.yml"], namers

    def test_the_inherit_is_named_on_the_caller(self) -> None:
        """The forward is refused where it is written, whatever it reaches."""
        inheritors = secret_inheritors(_probe(f"./{LOCAL_WORKFLOW_PATH}callee.yml"))
        assert inheritors == ["ci.yml:call"], inheritors

    def test_an_inherit_to_another_repository_is_named(self) -> None:
        """The callee there is code no clause here can read."""
        documents = _probe("octo/tools/.github/workflows/upload.yml@v1")
        inheritors = secret_inheritors(documents)
        holders = credential_holders(documents)

        assert inheritors == ["ci.yml:call"], inheritors
        assert holders == [], holders

    def test_a_callee_nothing_reachable_calls_is_outside(self) -> None:
        """The closure is not every workflow: an uncalled file stays out."""
        documents = _probe("octo/tools/.github/workflows/upload.yml@v1")
        namers = host_namers(documents)

        assert namers == [], namers


def _pull_request_workflow(job: dict[str, typ.Any]) -> dict[str, WorkflowDocument]:
    """Return one pull-request workflow holding *job*."""
    return typ.cast(
        "dict[str, WorkflowDocument]",
        {"ci.yml": {True: {"pull_request": None}, "jobs": {"a": job}}},
    )


class TestTheCredentialIsReadWhereverItIsReferenced:
    """A reference in any position a step or a call can read it from."""

    @pytest.mark.parametrize(
        "job",
        [
            pytest.param(
                {"steps": [{"run": f"echo ${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}"}]},
                id="run-body",
            ),
            pytest.param(
                {
                    "steps": [
                        {
                            "uses": "octo/upload@v1",
                            "with": {
                                "token": f"${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}"
                            },
                        }
                    ]
                },
                id="action-input",
            ),
            pytest.param(
                {"env": {"TOKEN": f"${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}"}},
                id="renamed-environment-value",
            ),
            pytest.param(
                {
                    "uses": "octo/tools/.github/workflows/upload.yml@v1",
                    "secrets": {"token": f"${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}"},
                },
                id="named-forward",
            ),
        ],
    )
    def test_the_reference_is_found(self, job: dict[str, typ.Any]) -> None:
        """Each position is a reference the credential clause must see."""
        holders = credential_holders(_pull_request_workflow(job))
        assert holders == ["ci.yml"], holders

    def test_a_named_forward_is_not_an_inherit(self) -> None:
        """Forwarding by name is the permitted shape; only ``inherit`` is refused."""
        job = {"uses": "octo/x/.github/workflows/y.yml@v1", "secrets": {"a": "b"}}

        inheritors = secret_inheritors(_pull_request_workflow(job))
        assert inheritors == [], inheritors


class TestEverySectionOfAWorkflowIsRead:
    """The reading walks the document, not a list of sections that run things."""

    def test_a_workflow_level_default_shell_is_read(self) -> None:
        """``defaults.run.shell`` wraps every `run:` step in the file.

        A reading of ``jobs`` and ``env`` alone passed a shell that reaches
        the host while every step in the file looked inert.
        """
        documents = typ.cast(
            "dict[str, WorkflowDocument]",
            {
                "ci.yml": {
                    True: {"pull_request": None},
                    "defaults": {
                        "run": {
                            "shell": "curl -sf https://api.codescene.io/v2 ; bash {0}"
                        }
                    },
                    "jobs": {"a": {"steps": [{"run": "true"}]}},
                }
            },
        )
        namers = host_namers(documents)
        assert namers == ["ci.yml"], namers

    def test_a_declared_reusable_workflow_secret_is_read(self) -> None:
        """A reachable callee declaring the credential asks to receive it."""
        documents = typ.cast(
            "dict[str, WorkflowDocument]",
            {
                "ci.yml": {
                    True: {"pull_request": None},
                    "jobs": {"call": {"uses": f"./{LOCAL_WORKFLOW_PATH}callee.yml"}},
                },
                "callee.yml": {
                    True: {"workflow_call": {"secrets": {CODESCENE_CREDENTIAL: None}}},
                    "jobs": {},
                },
            },
        )
        holders = credential_holders(documents)
        assert holders == ["callee.yml"], holders


def test_a_service_subcommand_in_a_callee_is_found() -> None:
    """The service clause runs over the closure too."""
    documents = typ.cast(
        "dict[str, WorkflowDocument]",
        {
            "ci.yml": {
                True: {"pull_request": None},
                "jobs": {"call": {"uses": f"./{LOCAL_WORKFLOW_PATH}callee.yml"}},
            },
            "callee.yml": {
                True: {"workflow_call": None},
                "jobs": {"a": {"steps": [{"run": "cs-coverage check coverage.xml"}]}},
            },
        },
    )

    callers = service_callers(documents)
    assert callers == {"callee.yml": ["cs-coverage check"]}, callers
