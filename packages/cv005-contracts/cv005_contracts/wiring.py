"""Hold the publisher's upload to the report its own job wrote earlier.

The uploader reads one file in one format; a generator after the upload,
in another job, or writing another file or format leaves it nothing
useful to send while every other clause passes.
"""

from __future__ import annotations

import re
import typing as typ

from .publisher import (
    COVERAGE_ACTION,
    invokes,
    position,
    upload_job,
    upload_step,
)
from .reading import steps

if typ.TYPE_CHECKING:
    from .loading import Document


def wiring_violations(document: Document) -> list[str]:
    """Require the upload to read the file, in the format, the publisher writes.

    The report must be written earlier in the upload's own job: a generator
    after the upload, or in another job, leaves the uploader nothing to read
    while every other clause passes. An omitted input reads as the action's
    own default, so two absent inputs compare as what each action does
    rather than as equal.

    Parameters
    ----------
    document : Document
        The publisher workflow document.

    Returns
    -------
    list[str]
        Every violation of the upload's wiring to an earlier generator.

    """
    upload = upload_step(document)
    job_steps = steps(upload_job(document))
    earlier = job_steps[: position(job_steps, upload)]
    written = [
        (_input(step, "output-path"), _format(step))
        for step in earlier
        if invokes(step, COVERAGE_ACTION)
    ]
    read = (_upload_path(upload), _format(upload))
    return (
        []
        if read in written or _merged_into(earlier, read)
        else [f"the upload reads {read!r}; earlier steps of its job write {written!r}"]
    )


def _input(step: dict[str, object], name: str) -> object:
    """Return one `with` input of a step, or None when it has none."""
    inputs = step.get("with")
    return inputs.get(name) if isinstance(inputs, dict) else None


def _merged_into(earlier: list[dict[str, object]], read: tuple[object, object]) -> bool:
    """Return whether an earlier `run` step merges the legs into the uploaded file.

    A publisher measuring several legs merges their reports with a command
    redirected to the file the upload reads, as
    `bun x lcov-result-merger 'lcov-*.info' > lcov.info` does. The merge
    counts only after a generator writing the upload's format, and only as
    the redirect target, not wherever the name appears.

    Examples
    --------
    >>> generator = {"uses": f"{COVERAGE_ACTION}@x", "with": {"format": "lcov"}}
    >>> merge = {"run": "merge 'lcov-*.info' > lcov.info"}
    >>> _merged_into([generator, merge], ("lcov.info", "lcov"))
    True
    >>> _merged_into([merge, generator], ("lcov.info", "lcov"))
    False

    """
    path, wanted = read
    target = re.compile(rf">\s*['\"]?{re.escape(str(path))}['\"]?\s*$", re.MULTILINE)
    has_generated = False
    for step in earlier:
        if invokes(step, COVERAGE_ACTION):
            has_generated = has_generated or _format(step) == wanted
        elif has_generated and target.search(str(step.get("run", ""))):
            return True
    return False


#: Both actions' `format` default.
DEFAULT_FORMAT: typ.Final[str] = "cobertura"
#: The uploader's `path` default, which it resolves from the format.
AUTO_PATH: typ.Final[str] = "__auto__"


def _format(step: dict[str, object]) -> object:
    """Return a coverage step's `format`, or the actions' default."""
    return _input(step, "format") or DEFAULT_FORMAT


def _upload_path(upload: dict[str, object]) -> object:
    """Return the file the uploader reads, resolving its `__auto__` default.

    Examples
    --------
    >>> _upload_path({"with": {"format": "lcov"}})
    'lcov.info'
    >>> _upload_path({"with": {"path": "cov.xml"}})
    'cov.xml'

    """
    path = _input(upload, "path")
    if path not in (None, "", AUTO_PATH):
        return path
    return "lcov.info" if _format(upload) == "lcov" else "coverage.xml"
