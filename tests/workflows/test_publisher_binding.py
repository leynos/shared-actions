"""The publisher's credential check and upload, driven on broken shapes.

This repository's publisher carries every part, so over it the contract
passes whether or not the readings can tell a part from its absence.

Run via ``make test``.
"""

from __future__ import annotations

import typing as typ

import pytest

from .publisher_binding import (
    ACCESS_TOKEN_VALUE,
    CHECK_COMMAND,
    credential_environments,
    upload_problems,
)

#: The check step as the publisher writes it.
CHECK: typ.Final[dict[str, typ.Any]] = {
    "id": "codescene-credential",
    "run": CHECK_COMMAND,
}
#: The upload step as the publisher writes it.
UPLOAD: typ.Final[dict[str, typ.Any]] = {
    "uses": "./.github/actions/upload-codescene-coverage",
    "if": (
        "steps.codescene-credential.outputs.available == 'true'"
        " && github.ref == 'refs/heads/main'"
    ),
    "with": {"mode": "upload", "access-token": ACCESS_TOKEN_VALUE},
}


def _publisher(*steps: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Return a one-job publisher running *steps* in order."""
    return {"jobs": {"upload": {"steps": list(steps)}}}


class TestTheUploadShape:
    """Every part present passes; each part missing is named."""

    def test_the_publisher_shape_is_complete(self) -> None:
        """Check before upload, guard on both terms, secret passed directly."""
        problems = upload_problems(_publisher(CHECK, UPLOAD))
        assert problems == [], problems

    @pytest.mark.parametrize(
        ("steps", "expected"),
        [
            pytest.param((UPLOAD,), "does not require", id="check-deleted"),
            pytest.param((UPLOAD, CHECK), "does not require", id="check-after"),
            pytest.param(
                ({**CHECK, "run": 'echo "available=true" >> "$GITHUB_OUTPUT"'}, UPLOAD),
                "does not require",
                id="command-changed",
            ),
            pytest.param(
                ({**CHECK, "if": "always()"}, UPLOAD),
                "does not require",
                id="check-guarded",
            ),
            pytest.param(
                (CHECK, {**UPLOAD, "if": "github.ref == 'refs/heads/main'"}),
                "does not require",
                id="output-term-dropped",
            ),
            pytest.param(
                (
                    CHECK,
                    {
                        **UPLOAD,
                        "with": {
                            "mode": "upload",
                            "access-token": "${{ env.CS_ACCESS_TOKEN }}",
                        },
                    },
                ),
                "access-token",
                id="token-from-env",
            ),
        ],
    )
    def test_a_missing_part_is_named(
        self, steps: tuple[dict[str, typ.Any], ...], expected: str
    ) -> None:
        """Deleting the check leaves the upload skipped forever, silently.

        The command is exact because ``false && echo ...`` still contains the
        text, and the check carries no ``if:`` for the same reason.
        """
        problems = upload_problems(_publisher(*steps))
        assert len(problems) == 1, problems
        assert expected in problems[0], problems


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            {"env": {"CS_ACCESS_TOKEN": "${{ secrets.CS_ACCESS_TOKEN }}"}},
            ["workflow"],
            id="workflow-level",
        ),
        pytest.param(
            {"jobs": {"a": {"env": {"TOKEN": "${{ secrets.CS_ACCESS_TOKEN }}"}}}},
            ["a"],
            id="job-level-renamed",
        ),
        pytest.param(
            {"jobs": {"a": {"steps": [{"env": {"CS_ACCESS_TOKEN": "x"}}]}}},
            ["a[0]"],
            id="step-level",
        ),
        pytest.param(_publisher(CHECK, UPLOAD), [], id="the-publisher-shape"),
    ],
)
def test_the_credential_is_found_in_any_environment(
    document: dict[str, typ.Any], expected: list[str]
) -> None:
    """A key or a value names it, at any of the three scopes."""
    found = credential_environments(document)
    assert found == expected, found
