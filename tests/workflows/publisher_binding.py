"""What the publisher's upload step is given, read positively.

The upload step is guarded on ``env.CS_ACCESS_TOKEN != ''``. A guard is a
prohibition: with the binding deleted it is simply false, the step skips on
every push, and publication stops without a red run anywhere. So the
contract asserts the binding itself: the credential is bound from the
repository secret in a scope the step sees, and the step hands it to the
action. Each function names what is missing, so a case can drive it on a
workflow written without one half.
"""

from __future__ import annotations

import typing as typ

from .workflow_boundary import CODESCENE_CREDENTIAL, _jobs, _steps, upload_steps

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The secret the binding must read from.
SECRET_REFERENCE: typ.Final[str] = f"secrets.{CODESCENE_CREDENTIAL}"
#: The action input that carries the credential into the upload. An input
#: name, not a credential.
ACCESS_TOKEN_INPUT: typ.Final[str] = "access-token"  # noqa: S105


def _binding(scope: object) -> str:
    """Return the value a scope's ``env`` binds the credential to, or ``""``."""
    if not isinstance(scope, dict):
        return ""
    env = scope.get("env")
    if not isinstance(env, dict):
        return ""
    return str(env.get(CODESCENE_CREDENTIAL, ""))


def _is_upload(step: dict[str, typ.Any], uploads: list[dict[str, typ.Any]]) -> bool:
    """Return whether *step* is one of the upload steps, by identity."""
    return any(step is upload for upload in uploads)


def missing_bindings(document: cabc.Mapping[typ.Any, typ.Any]) -> list[str]:
    """Name what an upload step in *document* lacks to receive the credential.

    A step sees its own ``env``, its job's and the workflow's; the nearest
    binding wins. The binding must read ``secrets.CS_ACCESS_TOKEN``, and the
    step must pass the credential to the action's ``access-token`` input.

    Examples
    --------
    >>> step = {"uses": "./.github/actions/upload-codescene-coverage",
    ...         "with": {"mode": "upload"}}
    >>> for problem in missing_bindings({"jobs": {"a": {"steps": [step]}}}):
    ...     print(problem)
    a: CS_ACCESS_TOKEN is not bound from secrets.CS_ACCESS_TOKEN
    a: access-token is not passed the credential
    """
    uploads = upload_steps(document)
    missing: list[str] = []
    for job_name, job in _jobs(document).items():
        for step in _steps(job):
            if not _is_upload(step, uploads):
                continue
            bound = _binding(step) or _binding(job) or _binding(document)
            if SECRET_REFERENCE not in bound:
                missing.append(
                    f"{job_name}: {CODESCENE_CREDENTIAL} is not bound from "
                    f"{SECRET_REFERENCE}"
                )
            passed = str((step.get("with") or {}).get(ACCESS_TOKEN_INPUT, ""))
            if CODESCENE_CREDENTIAL not in passed:
                missing.append(
                    f"{job_name}: {ACCESS_TOKEN_INPUT} is not passed the credential"
                )
    return missing
