"""How the publisher's upload step learns of, and receives, the credential.

A guard such as ``env.CS_ACCESS_TOKEN != ''`` is a prohibition: with the
binding deleted it is simply false, the upload skips on every push, and
nothing turns red. It also needs the token in an ``env``, and the upload
action is composite, so a step-level ``env`` reaches its nested artefact and
cache steps too. The shape asserted here keeps the token out of every
``env`` and states its presence positively:

- a check step with an ``id``, no ``if:``, and the sole command
  ``echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"``,
  whose expression evaluates before the shell runs;
- the upload step's ``if:`` requiring the trunk ref and, when it reads a check
  step's output at all, an earlier check step's, so the token's presence is
  never an ``env`` test; a guard of the trunk ref alone is the shape for a
  publisher whose upload action records a missing token itself;
- ``access-token`` passed ``${{ secrets.CS_ACCESS_TOKEN }}`` directly.

Each function names what is wrong, so a case can drive it on a workflow
written with one part missing.
"""

from __future__ import annotations

import re
import typing as typ

from .workflow_boundary import CODESCENE_CREDENTIAL, _jobs, _steps, upload_steps
from .workflow_expressions import TRUNK_REF_TERM, conjuncts, requires_every

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: A guard term reading a check step's ``available`` output.
_AVAILABLE_TERM: typ.Final[re.Pattern[str]] = re.compile(
    r"steps\.(?P<id>[A-Za-z_][A-Za-z0-9_-]*)\.outputs\.available == 'true'"
)
#: The check step's sole command, exactly.
CHECK_COMMAND: typ.Final[str] = (
    f"echo \"available=${{{{ secrets.{CODESCENE_CREDENTIAL} != '' }}}}\" "
    '>> "$GITHUB_OUTPUT"'
)
#: What the upload step passes the action, exactly.
ACCESS_TOKEN_VALUE: typ.Final[str] = f"${{{{ secrets.{CODESCENE_CREDENTIAL} }}}}"
#: The action input that carries the credential. An input name, not a secret.
ACCESS_TOKEN_INPUT: typ.Final[str] = "access-token"  # noqa: S105


def _is_check(step: cabc.Mapping[str, typ.Any]) -> bool:
    """Return whether *step* is the credential check, exactly as required."""
    return (
        str(step.get("run", "")).strip() == CHECK_COMMAND
        and bool(step.get("id"))
        and "if" not in step
    )


def _upload_problems(
    job_name: str, step: cabc.Mapping[str, typ.Any], checks: list[str]
) -> list[str]:
    """Name what one upload step lacks, given the ids of earlier checks."""
    problems: list[str] = []
    guard = str(step.get("if", ""))
    if not requires_every(guard, [TRUNK_REF_TERM]):
        problems.append(
            f"{job_name}: the upload's if: does not require the trunk ref: {guard!r}"
        )
    read = [
        match.group("id")
        for term in conjuncts(guard) or []
        if (match := _AVAILABLE_TERM.fullmatch(term)) is not None
    ]
    if any(check_id not in checks for check_id in read):
        problems.append(
            f"{job_name}: the upload's if: does not require an earlier check "
            f"step's output: {guard!r}"
        )
    passed = str((step.get("with") or {}).get(ACCESS_TOKEN_INPUT, "")).strip()
    if passed != ACCESS_TOKEN_VALUE:
        problems.append(
            f"{job_name}: {ACCESS_TOKEN_INPUT} is {passed!r}, not {ACCESS_TOKEN_VALUE}"
        )
    return problems


def upload_problems(document: cabc.Mapping[typ.Any, typ.Any]) -> list[str]:
    """Name what each upload step in *document* lacks.

    The upload's guard must require the trunk ref. If it also reads a check
    step's output, that check must come before the upload in the same job and
    be named by id.

    Examples
    --------
    >>> step = {"uses": "./.github/actions/upload-codescene-coverage",
    ...         "if": "github.ref == 'refs/heads/main'",
    ...         "with": {"mode": "upload"}}
    >>> len(upload_problems({"jobs": {"a": {"steps": [step]}}}))
    1
    """
    uploads = upload_steps(document)
    problems: list[str] = []
    for job_name, job in _jobs(document).items():
        checks: list[str] = []
        for step in _steps(job):
            if _is_check(step):
                checks.append(str(step["id"]))
            if any(step is upload for upload in uploads):
                problems.extend(_upload_problems(job_name, step, checks))
    return problems


def _env_scopes(
    document: cabc.Mapping[typ.Any, typ.Any],
) -> cabc.Iterator[tuple[str, object]]:
    """Yield every ``env`` in *document*, labelled by where it sits."""
    yield "workflow", document.get("env")
    for job_name, job in _jobs(document).items():
        yield job_name, job.get("env")
        for index, step in enumerate(_steps(job)):
            yield f"{job_name}[{index}]", step.get("env")


def credential_environments(document: cabc.Mapping[typ.Any, typ.Any]) -> list[str]:
    """Name every ``env`` in *document* that mentions the credential.

    A key or a value counts: the token may be bound under its own name or
    renamed.

    Examples
    --------
    >>> credential_environments({"env": {"T": "${{ secrets.CS_ACCESS_TOKEN }}"}})
    ['workflow']
    """
    return [
        where
        for where, env in _env_scopes(document)
        if isinstance(env, dict)
        and any(CODESCENE_CREDENTIAL in f"{key} {value}" for key, value in env.items())
    ]
