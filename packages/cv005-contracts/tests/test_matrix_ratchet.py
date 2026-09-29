"""A leg may take `with-ratchet` from a matrix value, and a pairing reads it.

ortho-config ratchets only on its Linux leg, through `with-ratchet:
${{ matrix.ratchet }}`. The estate rule reads the literal text, finds no
ratchet in the job and, without a pairing, refuses it. A pairing that declares
the ratcheting cell shows the leg ratchets there; these cases prove it counts,
and that a wrong or missing cell, or a second ratcheting leg, is still refused.
"""

from __future__ import annotations

import textwrap
import typing as typ

import pytest
from contract_fixtures import PIN, SHARED
from cv005_contracts import violations
from matrix_fixtures import write_repository

if typ.TYPE_CHECKING:
    from pathlib import Path

LANE = textwrap.dedent(f"""\
    name: CI
    on:
      push:
        branches: [main]
      pull_request:
    jobs:
      build-test:
        runs-on: ${{{{ matrix.runner }}}}
        permissions:
          contents: read
        strategy:
          matrix:
            include:
              - platform: linux
                runner: ubuntu-latest
                ratchet: true
              - platform: windows
                runner: windows-latest
                ratchet: false
        steps:
          - uses: actions/checkout@v4
          - name: Coverage (broad)
            if: github.event_name == 'pull_request'
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: broad.info
              format: lcov
              features: a b
              with-ratchet: ${{{{ matrix.ratchet }}}}
              publish-artefact: 'false'
          - name: Coverage (narrow)
            if: github.event_name == 'pull_request'
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: narrow.info
              format: lcov
              features: a
              publish-artefact: 'false'
    """)

PUBLISHER = textwrap.dedent(f"""\
    name: Coverage (main)
    on:
      push:
        branches: [main]
    permissions: {{}}
    concurrency:
      group: coverage-main-${{{{ github.ref }}}}
      cancel-in-progress: false
    jobs:
      coverage-upload:
        runs-on: ubuntu-latest
        environment: codescene
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
            with:
              persist-credentials: false
          - name: Coverage (broad)
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: broad.info
              format: lcov
              features: a b
              with-ratchet: 'true'
          - name: Coverage (narrow)
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: narrow.info
              format: lcov
              features: a
          - name: Merge
            run: merge '*.info' > lcov.info
          - name: Check for the CodeScene token
            id: codescene-token
            run: echo "available=${{{{ secrets.CS_ACCESS_TOKEN != '' }}}}" >> "$GITHUB_OUTPUT"
          - name: Upload coverage data to CodeScene
            if: steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main'
            uses: {SHARED}/upload-codescene-coverage@{PIN}
            with:
              path: lcov.info
              format: lcov
              access-token: ${{{{ secrets.CS_ACCESS_TOKEN }}}}
    """)  # noqa: E501 - two lines must match the real workflow's.

TREE = {"ci.yml": LANE, "coverage-main.yml": PUBLISHER}
BROAD = "ci.yml:build-test:Coverage (broad)"
NARROW = "ci.yml:build-test:Coverage (narrow)"
PUBLISHER_BROAD = "coverage-main.yml:coverage-upload:Coverage (broad)"
PUBLISHER_NARROW = "coverage-main.yml:coverage-upload:Coverage (narrow)"


def _config(ratchet: str = "true", *, narrow: bool = True) -> str:
    """Return a declaration pairing the broad leg, and the narrow one if asked."""
    text = f"""\
repository = "leynos/example"

[[pairing]]
lane = "{BROAD}"
publisher = "{PUBLISHER_BROAD}"
[pairing.matrix]
ratchet = "{ratchet}"
"""
    if narrow:
        text += f"""
[[pairing]]
lane = "{NARROW}"
publisher = "{PUBLISHER_NARROW}"
"""
    return text


def _found(root: Path, config: str, lane: str = LANE) -> list[str]:
    """Return the findings for the tree under a declaration."""
    texts = {**TREE, "ci.yml": lane}
    return [str(i) for i in violations(write_repository(root, texts, config))]


def test_a_declared_ratcheting_cell_counts_as_the_jobs_one_ratchet(
    tmp_path: Path,
) -> None:
    """The matrix supplies `with-ratchet` for the Linux cell, and the job passes."""
    assert _found(tmp_path, _config()) == []


def test_without_the_pairing_the_matrix_ratchet_is_not_read(tmp_path: Path) -> None:
    """The estate rule sees no literal ratchet and refuses the job."""
    found = _found(tmp_path, 'repository = "leynos/example"\n')
    assert any("found 0" in item for item in found), found


def test_a_declared_cell_that_does_not_ratchet_is_refused(tmp_path: Path) -> None:
    """Naming the Windows cell shows the leg ratchets nowhere in the job."""
    found = _found(tmp_path, _config(ratchet="false"))
    assert any("found 0" in item for item in found), found
    assert any("differs" in item and "with-ratchet" in item for item in found), found


def test_a_cell_the_matrix_lacks_is_refused(tmp_path: Path) -> None:
    """A value no cell carries proves nothing."""
    found = _found(tmp_path, _config(ratchet="maybe"))
    assert any("has no cell" in item for item in found), found


def test_a_second_ratcheting_leg_is_refused(tmp_path: Path) -> None:
    """The action keys the baseline by job, so two ratchets in a cell collide."""
    old = "          features: a\n          publish-artefact"
    assert LANE.count(old) == 1
    lane = LANE.replace(
        old,
        "          features: a\n          with-ratchet: ${{ matrix.ratchet }}\n"
        "          publish-artefact",
    )
    config = _config(narrow=False) + (
        f'\n[[pairing]]\nlane = "{NARROW}"\npublisher = "{PUBLISHER_NARROW}"\n'
        '[pairing.matrix]\nratchet = "true"\n'
    )
    found = _found(tmp_path, config, lane)
    assert any("found 2" in item for item in found), found


@pytest.mark.parametrize("value", ["'true'", "true"])
def test_a_literal_ratchet_still_counts_without_a_pairing(
    tmp_path: Path, value: str
) -> None:
    """The narrow direction: the estate rule is unchanged for literal values."""
    lane = LANE.replace("${{ matrix.ratchet }}", value)
    found = _found(tmp_path, 'repository = "leynos/example"\n', lane)
    assert not any("found 0" in item for item in found), found
