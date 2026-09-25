"""Tests for the shared reading boundary the runner contracts stand on.

`_workflow_reading.load_workflow` is the only place the placement, ceiling,
fork-fallback and check-name contracts turn a file into data. What it refuses
is what those contracts can never be shown, so the refusals are pinned here.
"""

from __future__ import annotations

import typing as typ

import pytest

from . import _workflow_reading as reading
from . import workflow_boundary as boundary

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestTheSharedReader:
    """What the shared loader refuses, and that it names the file."""

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            pytest.param(
                "jobs:\n  a:\n    runs-on: ubuntu-latest\n    runs-on: x\n",
                ValueError,
                id="duplicate-runs-on",
            ),
            pytest.param(
                "jobs:\n  a:\n    timeout-minutes: 10\n    timeout-minutes: 600\n",
                ValueError,
                id="duplicate-timeout",
            ),
            pytest.param("- a list\n", TypeError, id="not-a-mapping"),
            pytest.param("jobs: [a]\n", TypeError, id="jobs-not-a-mapping"),
        ],
    )
    def test_a_workflow_github_would_reject_is_refused(
        self, tmp_path: Path, text: str, error: type[Exception]
    ) -> None:
        """A second `runs-on` or ceiling is refused, not silently kept.

        PyYAML keeps the last of two equal keys, so a reader built on
        `yaml.safe_load` would hand the placement and ceiling rules whichever
        value came second while GitHub refuses the file outright.
        """
        (tmp_path / "lane.yml").write_text(text, encoding="utf-8")

        with pytest.raises(error, match=r"lane\.yml"):
            reading.load_workflow("lane.yml", directory=tmp_path)

    def test_a_well_formed_workflow_reads_through(self, tmp_path: Path) -> None:
        """The refusals are narrow: an ordinary workflow parses unchanged."""
        (tmp_path / "lane.yml").write_text(
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
        )

        document = reading.load_workflow("lane.yml", directory=tmp_path)

        assert document == {"jobs": {"a": {"runs-on": "ubuntu-latest"}}}, document


class TestRunnerPlatforms:
    """Which platforms the ratchet reader resolves a job's runners to."""

    @pytest.mark.parametrize(
        ("runs_on", "expected"),
        [
            pytest.param("${{ matrix.os }}", {"windows"}, id="dotted-reference"),
            pytest.param("${{ matrix['os'] }}", {"windows"}, id="bracketed-reference"),
            pytest.param(
                "${{ fork && 'ubuntu-latest' || 'ubicloud-standard-2' }}",
                # An expression that is not one bare reference over-reads
                # the whole matrix too, which only widens what the
                # publisher must cover.
                {"ubuntu", "windows"},
                id="fork-fallback-literals",
            ),
            pytest.param(
                "${{ fork && 'ubuntu-latest' || matrix['os'] }}",
                {"ubuntu", "windows"},
                id="literal-beside-a-bracketed-reference",
            ),
        ],
    )
    def test_a_matrix_key_is_never_a_runner(
        self, runs_on: str, expected: set[str]
    ) -> None:
        """`matrix['os']` names a dimension; its quoted key is not a runner.

        Read as a literal, `os` would become a platform of its own, and the
        publisher would be required to ratchet a platform that does not
        exist. The fork fallback's quoted labels, by contrast, are runners,
        and Ubicloud's are Linux.
        """
        document = {
            "jobs": {
                "lane": {
                    "runs-on": runs_on,
                    "strategy": {"matrix": {"os": ["windows-latest"]}},
                }
            }
        }

        found = boundary.platforms(document, "lane")

        assert found == expected, f"{runs_on!r} resolved to {found!r}"
