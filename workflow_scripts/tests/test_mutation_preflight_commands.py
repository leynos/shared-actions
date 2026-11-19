"""Declared commands: a required tool that is absent or wrong stops the run.

Each case is one of the failures issue #591 recorded: a missing mdtablefix, a
mold whose reported version does not extend the pin, a probe that exits
non-zero, and a probe that must run under the project's interpreter rather than
the helper's.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SHIMS_ONLY,
    Checkout,
    Outcome,
    outcome_for,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _commands_outcome(
    run_preflight: typ.Callable[..., Outcome], root: Path, manifest: str
) -> Outcome:
    """Run the preflight for a checkout declaring command requirements."""
    return outcome_for(
        run_preflight,
        root,
        Checkout(manifest=manifest),
        project_python="3.13",
    )


@dc.dataclass(frozen=True)
class CommandCase:
    """One declaration, the tool that answers it, and the verdict it earns."""

    tool: str
    body: str
    manifest: str
    status: str
    finding: str = ""


class TestCommands:
    """A required tool that is absent or wrong stops the run by name."""

    @POSIX_SHIMS_ONLY
    def test_a_missing_command_is_named(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        empty_path: None,
    ) -> None:
        """An absent command is the failure, and it is named."""
        outcome = _commands_outcome(
            run_preflight,
            tmp_path / "repo",
            '[[require_commands]]\nname = "mdtablefix"\n',
        )

        assert outcome.exit_code == 1
        assert "required command 'mdtablefix'" in outcome.findings("commands")
        assert "PATH" in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    @pytest.mark.parametrize(
        "case",
        [
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0 (compatible with GNU ld)"',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n',
                status="pass",
            ),
            CommandCase(
                tool="probe",
                body="exit 3",
                manifest='[[require_commands]]\nname = "probe"\n',
                status="pass",
            ),
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0"',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.4"\n',
                status="fail",
                finding="does not report '2.4'",
            ),
            CommandCase(
                tool="mold",
                body='echo "mold 2.41.0"; exit 3',
                manifest='[[require_commands]]\nname = "mold"\nversion = "2.41"\n',
                status="fail",
                finding="exited 3",
            ),
        ],
        ids=["pinned-pass", "unpinned-resolves", "nearby-version", "failed-probe"],
    )
    def test_a_command_is_judged_by_what_it_declares(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
        case: CommandCase,
    ) -> None:
        """A pinned version must match; an unpinned command must only resolve."""
        install_tool(case.tool, case.body)

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", case.manifest)

        assert outcome.phases["commands"]["status"] == case.status, outcome.stdout
        if case.finding:
            assert case.finding in outcome.findings("commands")

    @POSIX_SHIMS_ONLY
    def test_repeated_command_names_keep_their_own_evidence(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """Two entries for one tool must not leave only the later probe's result."""
        install_tool("probe", 'echo "probe 2.4.1"')
        manifest = (
            "[[require_commands]]\n"
            'name = "probe"\nversion = "2.4.1"\n'
            "[[require_commands]]\n"
            'name = "probe"\nversion = "2.4"\n'
            'version_args = ["--report"]\n'
        )

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", manifest)

        assert outcome.phases["commands"]["status"] == "pass", outcome.stdout
        results = typ.cast(
            "list[dict[str, object]]", outcome.detail("commands")["results"]
        )
        assert [(item["index"], item["name"]) for item in results] == [
            (0, "probe"),
            (1, "probe"),
        ], "each declaration entry must keep its own indexed evidence"

    @POSIX_SHIMS_ONLY
    def test_a_version_probe_selects_the_validated_project_interpreter(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        install_tool: typ.Callable[..., None],
    ) -> None:
        """A probe that runs the project must not inherit another interpreter."""
        # The shim fails unless the run passed the project interpreter to it,
        # so the phase's verdict is the assertion rather than a record of it.
        install_tool(
            "probe",
            'echo "probe 2.4.1"\ntest "$UV_PYTHON" = "3.13" || exit 9',
        )
        manifest = '[[require_commands]]\nname = "probe"\nversion = "2.4.1"\n'

        outcome = _commands_outcome(run_preflight, tmp_path / "repo", manifest)

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["commands"]["status"] == "pass", (
            f"the probe must see UV_PYTHON=3.13: {outcome.findings('commands')}"
        )
