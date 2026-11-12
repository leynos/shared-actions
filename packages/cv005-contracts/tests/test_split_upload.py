"""Cases for a publisher whose CodeScene upload is its own job.

The measuring job publishes the report as an artefact; a job it `needs`, in
the `codescene` environment, downloads it and uploads it. The wiring rule must
accept exactly that hand-over and refuse every way it could leave the uploader
nothing to read, while every other clause still holds the upload job.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PUBLISHER, TREE, tree, violations

CHECK_AND_UPLOAD = PUBLISHER[
    PUBLISHER.index("      - name: Check for the CodeScene token") :
]
MEASURING = PUBLISHER.replace(CHECK_AND_UPLOAD, "").replace(
    "        environment: codescene\n", ""
)
PUBLISH = (
    "      - name: Archive the report for the upload job\n"
    "        uses: actions/upload-artifact@v4\n"
    "        with:\n"
    "          name: coverage-report\n"
    "          path: coverage.xml\n"
)
DOWNLOAD = (
    "      - name: Fetch the report\n"
    "        uses: actions/download-artifact@v4\n"
    "        with:\n"
    "          name: coverage-report\n"
)
UPLOAD_JOB = (
    "  codescene-upload:\n"
    "    needs: coverage-upload\n"
    "    runs-on: ubuntu-latest\n"
    "    environment: codescene\n"
    "    permissions:\n"
    "      contents: read\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          persist-credentials: false\n"
    f"{DOWNLOAD}" + CHECK_AND_UPLOAD
)


def _split(*, publish: str = PUBLISH, upload_job: str = UPLOAD_JOB) -> str:
    """Return the fixture publisher with its upload moved to its own job."""
    return MEASURING.rstrip("\n") + "\n" + publish + upload_job


def test_the_fixture_is_clean_before_any_mutation() -> None:
    """The split fixture itself raises nothing the compliant tree does not."""
    assert violations(tree(coverage_main=_split())) == violations(TREE)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # No download at all: the uploader reads an empty workspace.
        (DOWNLOAD, ""),
        # Another artefact name.
        ("          name: coverage-report\n    ", "          name: other\n    "),
        # The download may be skipped.
        (
            "      - name: Fetch the report\n",
            "      - name: Fetch the report\n        if: always()\n",
        ),
        # It lands in another directory.
        (DOWNLOAD, DOWNLOAD + "          path: elsewhere\n"),
    ],
)
def test_a_broken_download_is_refused(old: str, new: str) -> None:
    """Every way the download can miss the uploaded file is refused."""
    text = _split(upload_job=UPLOAD_JOB.replace(old, new, 1))
    assert text != _split()
    found = violations(tree(coverage_main=text))
    assert any("the upload reads" in f for f in found), found


@pytest.mark.parametrize(
    "publish",
    [
        # The archive step may be skipped.
        PUBLISH.replace("        uses:", "        if: always()\n        uses:"),
        # It publishes another file than the generator's output.
        PUBLISH.replace("path: coverage.xml", "path: other.xml"),
        # Nothing is archived.
        "",
    ],
)
def test_a_broken_archive_is_refused(publish: str) -> None:
    """The measuring job must always publish what its generator wrote."""
    found = violations(tree(coverage_main=_split(publish=publish)))
    assert any("the upload reads" in f for f in found), found


def test_an_upload_job_that_does_not_need_the_measuring_job_is_refused() -> None:
    """Without `needs` the download has no guarantee the artefact exists."""
    text = _split(upload_job=UPLOAD_JOB.replace("    needs: coverage-upload\n", ""))
    found = violations(tree(coverage_main=text))
    assert any("the upload reads" in f for f in found), found


def test_an_expression_naming_the_artefact_proves_nothing() -> None:
    """`${{ github.job }}` evaluates to a different name in each job."""
    publish = PUBLISH.replace("coverage-report", "report-${{ github.job }}")
    download = DOWNLOAD.replace("coverage-report", "report-${{ github.job }}")
    text = _split(publish=publish, upload_job=UPLOAD_JOB.replace(DOWNLOAD, download))
    found = violations(tree(coverage_main=text))
    assert any("the upload reads" in f for f in found), found
