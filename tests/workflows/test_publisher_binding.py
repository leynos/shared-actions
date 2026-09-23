"""The publisher's credential binding, driven on workflows missing one half.

This repository's publisher carries both halves, so over it the contract
passes whether or not the reading can tell a binding from its absence.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from .publisher_binding import missing_bindings

#: The upload step as the publisher writes it.
UPLOAD: typ.Final[dict[str, typ.Any]] = {
    "uses": "./.github/actions/upload-codescene-coverage",
    "if": "github.ref == 'refs/heads/main' && env.CS_ACCESS_TOKEN != ''",
    "with": {"mode": "upload", "access-token": "${{ env.CS_ACCESS_TOKEN }}"},
}
#: The job-level binding the publisher uses.
BOUND: typ.Final[dict[str, str]] = {
    "CS_ACCESS_TOKEN": "${{ secrets.CS_ACCESS_TOKEN || '' }}"
}


def _publisher(
    *, job_env: dict[str, str] | None, step: dict[str, typ.Any]
) -> dict[str, typ.Any]:
    """Return a one-job publisher with the given binding and upload step."""
    job: dict[str, typ.Any] = {"steps": [step]}
    if job_env is not None:
        job["env"] = job_env
    return {"jobs": {"upload": job}}


def test_the_publisher_shape_is_complete() -> None:
    """Both halves present: nothing is missing."""
    missing = missing_bindings(_publisher(job_env=BOUND, step=UPLOAD))
    assert missing == [], missing


@pytest.mark.parametrize(
    ("job_env", "step", "expected"),
    [
        pytest.param(
            None,
            UPLOAD,
            "upload: CS_ACCESS_TOKEN is not bound from secrets.CS_ACCESS_TOKEN",
            id="binding-deleted",
        ),
        pytest.param(
            {"CS_ACCESS_TOKEN": "${{ vars.SOMETHING }}"},
            UPLOAD,
            "upload: CS_ACCESS_TOKEN is not bound from secrets.CS_ACCESS_TOKEN",
            id="bound-from-elsewhere",
        ),
        pytest.param(
            BOUND,
            {**UPLOAD, "with": {"mode": "upload"}},
            "upload: access-token is not passed the credential",
            id="input-deleted",
        ),
    ],
)
def test_a_missing_half_is_named(
    job_env: dict[str, str] | None, step: dict[str, typ.Any], expected: str
) -> None:
    """Either half deleted leaves the guard false and the upload skipped."""
    missing = missing_bindings(_publisher(job_env=job_env, step=step))
    assert missing == [expected], missing


def test_a_step_level_binding_counts() -> None:
    """The nearest scope binds; a step-level binding is as good as a job's."""
    step = {**UPLOAD, "env": BOUND}
    missing = missing_bindings(_publisher(job_env=None, step=step))
    assert missing == [], missing
