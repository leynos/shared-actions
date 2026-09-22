"""Tests for the readers behind the Whitaker installer floor contract.

`test_whitaker_installer_floor.py` holds the rules. This module holds
what those rules depend on being true of their readers: which `uses:`
values name the action, which scopes resolve a version, which files the
scan reads, and what the load boundary refuses. Kept apart so the rule
module reads as the contract it states, and each half stays small
enough to review.
"""

from __future__ import annotations

import re
import typing as typ

import pytest

from . import test_whitaker_installer_floor as floor

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestTheActionReference:
    """Which `uses:` values this contract reads as the Whitaker action."""

    @pytest.mark.parametrize(
        ("uses", "expected"),
        [
            pytest.param("./.github/actions/install-whitaker", True, id="workspace"),
            pytest.param("$/.github/actions/install-whitaker", True, id="self"),
            pytest.param("./.github/actions/install-whitaker/", True, id="slash"),
            pytest.param("$/.github/actions/install-mdtablefix", False, id="other"),
            pytest.param(
                "./.github/actions/install-whitaker-next", False, id="longer-name"
            ),
            pytest.param(
                "leynos/shared-actions/.github/actions/install-whitaker@v1",
                False,
                id="pinned-remote",
            ),
            pytest.param(None, False, id="run-step"),
        ],
    )
    def test_the_action_is_recognized_by_shape(
        self,
        uses: str | None,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Both local prefixes name the action, and nothing else does.

        GitHub runs `$/` and `./` against the same directory, so a lane
        written either way must be read; a pinned remote reference runs
        another commit's action and is outside this contract.
        """
        assert floor._installs_whitaker(uses) is expected, (
            f"{uses!r} should {'' if expected else 'not '}be read as the "
            "Whitaker action"
        )

    def test_a_self_reference_below_the_floor_is_refused(self) -> None:
        """A `$/` lane asking for 0.2.6 reaches the floor rule and fails it.

        The recognition case above could pass while the lane reader
        compared the spelling some other way, so this drives a whole
        workflow through the reader and the assertion together.
        """
        workflow: dict[object, object] = {
            "jobs": {
                "lint": {
                    "steps": [
                        {
                            "uses": "$/.github/actions/install-whitaker",
                            "with": {floor.VERSION_INPUT: "0.2.6"},
                        }
                    ]
                }
            }
        }

        lanes = list(floor._lanes("ci.yml", workflow))

        assert lanes == [("ci.yml", "lint", "0.2.6")], lanes
        with pytest.raises(AssertionError, match=re.escape("below the 0.2.7 floor")):
            floor._assert_at_or_above_floor(lanes[0][2], subject="ci.yml::lint")


class TestTheYamlBoundary:
    """What the reader does with a document whose shape has drifted."""

    @pytest.mark.parametrize(
        "workflow",
        [
            pytest.param({"jobs": ["lint"]}, id="jobs-as-a-list"),
            pytest.param(
                {"jobs": {"lint": {"steps": "make lint"}}}, id="steps-as-text"
            ),
            pytest.param(
                {
                    "jobs": {
                        "lint": {
                            "steps": [
                                {
                                    "uses": "./.github/actions/install-whitaker",
                                    "with": ["installer-version"],
                                }
                            ]
                        }
                    }
                },
                id="with-as-a-list",
            ),
        ],
    )
    def test_a_drifted_shape_fails_rather_than_reading_as_empty(
        self, workflow: dict[object, object]
    ) -> None:
        """A container of the wrong type raises instead of hiding its lanes.

        Reading any of these as "no jobs", "no steps" or "no inputs" would
        drop an install step from the floor rule without a word.
        """
        with pytest.raises(TypeError):
            list(floor._lanes("ci.yml", workflow))


def _lane_workflow(
    supplied: str,
    *,
    step_env: dict[str, str] | None = None,
    job_env: dict[str, str] | None = None,
    workflow_env: dict[str, str] | None = None,
) -> dict[object, object]:
    """Return a one-lane workflow asking for *supplied*, with optional scopes."""
    step: dict[object, object] = {
        "uses": "./.github/actions/install-whitaker",
        "with": {floor.VERSION_INPUT: supplied},
    }
    job: dict[object, object] = {"steps": [step]}
    workflow: dict[object, object] = {"jobs": {"lint": job}}
    for scope, environment in (
        (step, step_env),
        (job, job_env),
        (workflow, workflow_env),
    ):
        if environment is not None:
            scope["env"] = environment
    return workflow


class TestVersionResolution:
    """How a lane's `installer-version` is resolved before it is compared."""

    @pytest.mark.parametrize(
        ("scopes", "expected"),
        [
            pytest.param(
                {"step_env": {"V": "0.2.9"}, "job_env": {"V": "0.2.1"}},
                "0.2.9",
                id="step-over-job",
            ),
            pytest.param(
                {"job_env": {"V": "0.2.9"}, "workflow_env": {"V": "0.2.1"}},
                "0.2.9",
                id="job-over-workflow",
            ),
            pytest.param({"workflow_env": {"V": "0.2.9"}}, "0.2.9", id="workflow"),
            pytest.param({}, "${{ env.V }}", id="unresolved-stays-verbatim"),
        ],
    )
    def test_the_nearest_scope_decides(
        self, scopes: dict[str, dict[str, str]], expected: str
    ) -> None:
        """Step, then job, then workflow, as GitHub searches them.

        A name no scope defines is returned as written, so the floor rule
        below refuses it as unreadable rather than guessing a version.
        """
        lanes = list(floor._lanes("ci.yml", _lane_workflow("${{ env.V }}", **scopes)))

        assert lanes == [("ci.yml", "lint", expected)], lanes

    def test_another_jobs_environment_does_not_resolve_it(self) -> None:
        """A name defined only in a sibling job stays unresolved.

        The runner never searches another job's `env`, so reading one
        would let an unrelated job decide this lane's installer.
        """
        workflow = _lane_workflow("${{ env.V }}")
        jobs = floor._mapping(workflow["jobs"], subject="jobs")
        jobs["other"] = {"env": {"V": "0.2.9"}, "steps": []}

        assert list(floor._lanes("ci.yml", workflow)) == [
            ("ci.yml", "lint", "${{ env.V }}")
        ]

    @pytest.mark.parametrize(
        "requested",
        [
            pytest.param("${{ env.V }}", id="unresolved-name"),
            pytest.param("${{ inputs.version }}", id="unsupported-expression"),
            pytest.param("v0.2.8", id="prefixed"),
            pytest.param("0.2.8.1", id="four-components"),
            pytest.param("0.2.x", id="wildcard"),
            pytest.param("", id="empty"),
        ],
    )
    def test_an_unreadable_version_is_refused(self, requested: str) -> None:
        """Anything but one to three numeric components fails as unreadable.

        Accepting it as "probably fine" is the direction that hides a
        lane below the floor, so the rule refuses what it cannot compare.
        """
        with pytest.raises(AssertionError, match="not a version this rule can read"):
            floor._assert_at_or_above_floor(requested, subject="ci.yml::lint")


class TestTheRepositoryReader:
    """What the directory scan and the load boundary accept and refuse."""

    def test_every_workflow_extension_is_scanned(self, tmp_path: Path) -> None:
        """`.yml` and `.yaml` in any case are read; other files are not.

        A `.yaml` lane asking for 0.2.6 must reach the floor rule, which it
        cannot if the scan skips its extension.
        """
        lane = (
            "jobs:\n"
            "  lint:\n"
            "    steps:\n"
            "      - uses: ./.github/actions/install-whitaker\n"
            "        with:\n"
            "          installer-version: '0.2.6'\n"
        )
        (tmp_path / "a.yaml").write_text(lane, encoding="utf-8")
        (tmp_path / "b.YML").write_text(lane, encoding="utf-8")
        (tmp_path / "notes.txt").write_text(lane, encoding="utf-8")
        (tmp_path / "c.yml").mkdir()

        assert floor._install_whitaker_steps(tmp_path) == [
            ("a.yaml", "lint", "0.2.6"),
            ("b.YML", "lint", "0.2.6"),
        ]

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            pytest.param("jobs: [unclosed\n", ValueError, id="invalid-yaml"),
            pytest.param("- a list\n", TypeError, id="not-a-mapping"),
        ],
    )
    def test_an_unreadable_document_fails_naming_the_file(
        self, tmp_path: Path, text: str, error: type[Exception]
    ) -> None:
        """A parse failure and a shape failure both name the file."""
        path = tmp_path / "broken.yml"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(error, match=re.escape(str(path))):
            floor._load(path)

    def test_the_action_default_is_read_from_the_given_manifest(
        self, tmp_path: Path
    ) -> None:
        """The default comes from the manifest passed in, not a fixed path."""
        manifest = tmp_path / "action.yml"
        manifest.write_text(
            "inputs:\n  installer-version:\n    default: '0.2.6'\n", encoding="utf-8"
        )

        assert floor._action_default(manifest) == "0.2.6"
