"""Rules for where the publisher's CodeScene credential may appear.

The credential reaches exactly two places. A check step, which binds
nothing and runs one exact command, records whether the secret is set;
the upload step's guard reads that output and the step passes the secret
to the uploader's `access-token` input. The uploader is a composite action
that hands its step's `env` to the nested artefact and cache steps it
runs, so the token must not sit in that `env`, nor in any other.
"""

from __future__ import annotations

import typing as typ

from .publisher import CHECK_STEP_ID, position, upload_job, upload_step
from .reach import unnamed_secret_references
from .reading import jobs, steps, texts

if typ.TYPE_CHECKING:
    from .loading import Document

#: The check step's sole command. The expression evaluates to `true` or
#: `false` before the shell starts, so the step binds nothing and holds no
#: shell conditional that a prefix such as `false &&` could neutralize.
CHECK_COMMAND: typ.Final[str] = (
    'echo "available=${{ secrets.CS_ACCESS_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"'
)

#: The only keys the check step may carry: no `if:`, `env`, `shell` or
#: `continue-on-error` can change what its one command does.
CHECK_STEP_KEYS: typ.Final[frozenset[str]] = frozenset({"name", "id", "run"})


def check_step(document: Document) -> dict[str, object] | None:
    """Return the upload job's one token check step, or None if it has not one.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    dict[str, object] | None
        The check step, or None if the upload job holds no such step.

    """
    found = [
        step for step in steps(upload_job(document)) if step.get("id") == CHECK_STEP_ID
    ]
    return found[0] if len(found) == 1 else None


def check_step_violations(document: Document) -> list[str]:
    """Require the token check to run its one exact command before the upload.

    Without the check step the upload guard reads a missing output as
    empty and skips for ever, with nothing failing anywhere, so the step
    is asserted positively rather than inferred from the guard.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the token check step's requirements.

    """
    job_steps = steps(upload_job(document))
    check = check_step(document)
    if check is None:
        return [f"the upload job must hold one `{CHECK_STEP_ID}` step"]
    extra = sorted(str(key) for key in check if key not in CHECK_STEP_KEYS)
    problems = [f"the check step may not declare {key!r}" for key in extra]
    problems.extend(_run_defaults_violations(document))
    if str(check.get("run", "")).strip() != CHECK_COMMAND:
        problems.append(f"the check step must run exactly {CHECK_COMMAND!r}")
    if position(job_steps, check) > position(job_steps, upload_step(document)):
        problems.append("the check step must run before the upload step")
    return problems


def _run_defaults_violations(document: Document) -> list[str]:
    """Refuse `defaults.run` on the workflow or the upload job.

    A default shell or working directory reshapes the check step as a
    step-level `shell` would: `bash -c 'exit 0; {0}'` skips its command, the
    output is never written, and the upload skips for ever.

    Returns
    -------
    list[str]
        One violation for each scope that declares `defaults.run`.

    """
    holders = (("workflow", document), ("upload job", upload_job(document)))
    return [
        f"the {scope} may not set `defaults.run`; it reshapes the check step"
        for scope, holder in holders
        if _sets_run_defaults(holder)
    ]


def _sets_run_defaults(holder: dict[object, object] | dict[str, object]) -> bool:
    """Return whether a workflow or job declares `defaults.run`."""
    defaults = holder.get("defaults")
    return isinstance(defaults, dict) and "run" in defaults


def _without_permitted_references(document: Document) -> list[object]:
    """Return the publisher's parts with the two permitted references removed.

    The check step's command and the upload step's `access-token` input are
    the only places the credential may appear; everything else, the
    workflow's and each job's `env` included, is returned for the sweep.

    Returns
    -------
    list[object]
        The workflow and job fields, and every step, less the two
        permitted references.

    """
    upload = upload_step(document)
    check = check_step(document)
    parts: list[object] = [
        {key: value for key, value in document.items() if key != "jobs"}
    ]
    for job in jobs(document).values():
        parts.append({key: value for key, value in job.items() if key != "steps"})
        parts.extend(_permitted_removed(step, check, upload) for step in steps(job))
    return parts


def _permitted_removed(
    step: dict[str, object],
    check: dict[str, object] | None,
    upload: dict[str, object],
) -> dict[str, object]:
    """Return one step with the reference it is permitted to hold removed."""
    if step is check:
        return {key: value for key, value in step.items() if key != "run"}
    if step is upload:
        return _upload_without_token_input(step)
    return step


def _upload_without_token_input(step: dict[str, object]) -> dict[str, object]:
    """Return the upload step with its `access-token` input removed."""
    inputs = step.get("with")
    if not isinstance(inputs, dict):
        return step
    kept = {key: value for key, value in inputs.items() if key != "access-token"}
    return {**step, "with": kept}


def token_scope_violations(document: Document) -> list[str]:
    """Refuse the credential anywhere in the publisher but its two uses.

    An `env` binding at any level is refused with the rest: the uploader
    binds the token itself from its input, and a step-level binding would
    reach the nested steps of that composite action.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the credential's permitted scope.

    """
    return [
        f"the credential appears outside the check and the upload input: {text!r}"
        for part in _without_permitted_references(document)
        for text in texts(part)
        if "cs_access_token" in text.casefold()
    ] + [
        f"the publisher reads secrets without naming one: {text!r}"
        for text in unnamed_secret_references(document)
    ]
