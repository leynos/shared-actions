"""Hold the publisher's upload to the report its own job wrote earlier.

The uploader reads one file in one format; a generator after the upload,
in another job, or writing another file or format leaves it nothing
useful to send while every other clause passes.
"""

from __future__ import annotations

import posixpath
import re
import typing as typ

from .publisher import (
    COVERAGE_ACTION,
    generates_coverage,
    invokes,
    position,
    upload_job,
    upload_step,
)
from .reading import jobs, steps

if typ.TYPE_CHECKING:
    from .loading import Document


def wiring_violations(document: Document) -> list[str]:
    """Require the upload to read the file, in the format, the publisher writes.

    The report must be written earlier in the upload's own job, or handed
    over from a job the upload job `needs` through an unconditional
    `upload-artifact` and `download-artifact` pair naming the same artefact: a
    generator after the upload, or in another job with no such hand-over,
    leaves the uploader nothing to read
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
        (_normalised(_input(step, "output-path")), _format(step))
        for step in earlier
        if generates_coverage(step)
    ] + _handed_over(document, earlier)
    read = (_normalised(_upload_path(upload)), _format(upload))
    return (
        []
        if read in written or _merged_into(earlier, read)
        else [f"the upload reads {read!r}; earlier steps of its job write {written!r}"]
    )


DOWNLOAD_ACTION: typ.Final[str] = "actions/download-artifact"
UPLOAD_ARTEFACT_ACTION: typ.Final[str] = "actions/upload-artifact"


def _reliable(step: dict[str, object]) -> bool:
    """Return whether a step always runs and fails its job when it fails."""
    return "if" not in step and "continue-on-error" not in step


def _needs(job: dict[str, object]) -> list[object]:
    """Return a job's `needs` as a list, which YAML allows to be a scalar.

    Examples
    --------
    >>> _needs({"needs": "a"}), _needs({"needs": ["a", "b"]}), _needs({})
    (['a'], ['a', 'b'], [])

    """
    needed = job.get("needs") or []
    return (
        [needed] if isinstance(needed, str) else list(typ.cast("list[object]", needed))
    )


def _handed_over(
    document: Document, earlier: list[dict[str, object]]
) -> list[tuple[object, object]]:
    """Return the reports a job the upload job needs hands over before the upload.

    A report counts when an earlier, unconditional `download-artifact` step of
    the upload job names an artefact that an unconditional `upload-artifact`
    step of a needed job, running after that job's generator, publishes from
    the generator's output path. The downloaded file is the output path's
    basename under the download's `path`.
    """
    all_jobs = jobs(document)
    producers = [
        all_jobs[name]
        for name in _needs(upload_job(document))
        if isinstance(name, str) and name in all_jobs
    ]
    downloads = [
        step for step in earlier if invokes(step, DOWNLOAD_ACTION) and _this_run(step)
    ]
    return [
        (_landed(download, written), _format(generator))
        for download in downloads
        for producer in producers
        for generator, written in _published(producer, _input(download, "name"))
    ]


#: Download inputs that would read an artefact of another run or repository.
FOREIGN_INPUTS: typ.Final[tuple[str, ...]] = ("github-token", "repository", "run-id")


def _this_run(step: dict[str, object]) -> bool:
    """Return whether a download always runs and reads only this run's artefacts."""
    path = str(_input(step, "path") or "")
    # `download-artifact` expands `~`; the uploader reads its path literally.
    return (
        _reliable(step)
        and not any(_input(step, key) for key in FOREIGN_INPUTS)
        and not path.startswith("~")
        # An expression may resolve to `~` or to a path the uploader reads
        # differently, so equal text proves nothing.
        and "${{" not in path
    )


def _landed(download: dict[str, object], written: object) -> str:
    """Return where a download puts the file a generator wrote."""
    directory = str(_input(download, "path") or ".")
    return posixpath.normpath(
        posixpath.join(directory, posixpath.basename(str(written)))
    )


def _normalised(path: object) -> object:
    """Return a path with `./` and doubled separators removed; None stays None.

    Examples
    --------
    >>> _normalised("./coverage.xml"), _normalised(None)
    ('coverage.xml', None)

    """
    return None if path is None else posixpath.normpath(str(path))


def _published(
    producer: dict[str, object], artefact: object
) -> list[tuple[dict[str, object], object]]:
    """Return the (generator, output path) pairs a job publishes as `artefact`."""
    if not artefact or "${{" in str(artefact):
        # An expression may name different artefacts in the two jobs
        # (`${{ github.job }}`), so equal text proves nothing.
        return []
    producer_steps = steps(producer)
    return [
        (generator, _input(generator, "output-path"))
        for index, publish in enumerate(producer_steps)
        if _publishes(publish, artefact)
        for generator in producer_steps[:index]
        if _writes(generator, _input(publish, "path"))
        # An expression in the path may evaluate differently in each job.
        and "${{" not in str(_input(publish, "path"))
    ]


def _publishes(step: dict[str, object], artefact: object) -> bool:
    """Return whether a step always archives the named artefact."""
    return (
        invokes(step, UPLOAD_ARTEFACT_ACTION)
        and _reliable(step)
        and (_input(step, "name") or "artifact") == artefact
        and _includes_hidden(step)
    )


def _includes_hidden(step: dict[str, object]) -> bool:
    """Return whether the archive keeps a hidden report.

    `upload-artifact` leaves out hidden files unless told otherwise, so a
    report whose path has a dot-prefixed segment needs `include-hidden-files`.
    """
    parts = str(_input(step, "path") or "").split("/")
    hidden = any(part.startswith(".") and part not in {".", ".."} for part in parts)
    return not hidden or str(_input(step, "include-hidden-files")).lower() == "true"


def _writes(step: dict[str, object], path: object) -> bool:
    """Return whether a step is a generator whose output path is `path`."""
    return (
        generates_coverage(step)
        and bool(path)
        and _normalised(_input(step, "output-path")) == _normalised(path)
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
    counts only after a generator writing the upload's format, only as the
    redirect target, not wherever the name appears, and only when the step
    always runs and cannot fail green.

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
    target = re.compile(
        rf">\s*['\"]?(?:\./)?{re.escape(str(path))}['\"]?\s*$", re.MULTILINE
    )
    has_generated = False
    for step in earlier:
        if invokes(step, COVERAGE_ACTION):
            has_generated = has_generated or _format(step) == wanted
        elif has_generated and _is_reliable_merge(step, target):
            return True
    return False


def _is_reliable_merge(step: dict[str, object], target: re.Pattern[str]) -> bool:
    """Return whether a step redirects to the uploaded file and always runs.

    A merge under `if:` may be skipped, and one with `continue-on-error` may
    fail green, either leaving the uploader a stale or missing report while
    the clause passes.
    """
    return (
        "if" not in step
        and "continue-on-error" not in step
        and target.search(str(step.get("run", ""))) is not None
    )


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
