"""The mutation preflight names what a run would fail on, before it starts.

Each case here is one of the failures issue #591 recorded: a missing README, a
hidden workflow tree, a generator sibling, a Rust toolchain file, a nested
fixture, an interpreter outside the project's range, an absent mold or
mdtablefix. The assertion in each case is that the phase fails *naming the
path, command or boundary*, and the paired case shows the same repository
passing once the requirement is declared or satisfied.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

import pytest

from workflow_scripts.mutationpreflight import cli
from workflow_scripts.tests.mutation_preflight_support import (
    RULE_AVAILABLE,
    Checkout,
    Outcome,
    outcome_for,
    repository,
)


def _materializable(declared: str) -> bool:
    """Return whether *declared* names a path inside the fixture checkout."""
    path = Path(declared)
    return not path.is_absolute() and ".." not in path.parts


class TestManifest:
    """The declaration is read, or refused, before anything is trusted."""

    def test_a_repository_declaring_nothing_is_a_clean_no_op(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declaration is not a failure: only the interpreter is checked."""
        outcome = outcome_for(
            run_preflight, tmp_path / "repo", Checkout(), project_python="3.13"
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.result == "pass", outcome.stdout
        assert outcome.phases["manifest"]["status"] == "skipped", (
            "the manifest phase must report skipped",
            outcome.stdout,
        )
        assert set(outcome.phases) == {
            "manifest",
            "interpreter",
            "native",
            "sandbox",
            "commands",
            "checks",
        }, "a phase the declaration would have enabled is reported, not omitted"
        for phase in ("native", "sandbox", "commands", "checks"):
            assert outcome.phases[phase]["status"] == "skipped", phase
            reason = str(outcome.detail(phase)["reason"])
            assert "declares no mutation environment" in reason, (phase, reason)

    def test_two_declarations_are_refused_rather_than_ranked(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Which file binds the run must not depend on a precedence rule."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = ["README.md"]\n',
                extra={"README.md": "# example\n"},
            ),
        )
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool.mutation-preflight]\nsandbox_paths = ["README.md"]\n')

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "keep one" in outcome.findings("manifest"), (
            "the finding must name the duplicate declarations"
        )
        for phase in ("native", "sandbox", "commands", "checks"):
            assert outcome.phases[phase]["status"] == "skipped", phase
            reason = str(outcome.detail(phase)["reason"])
            assert "was refused" in reason, (
                "a refused manifest must not be half-applied to later phases",
                phase,
                reason,
            )

    def test_an_unrecognized_key_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A typo would otherwise read as a requirement that is being checked."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest='sandbox_path = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "sandbox_path" in outcome.findings("manifest"), (
            "the finding must name the unrecognised key"
        )
        assert "supported keys" in outcome.findings("manifest"), (
            "the finding must name the supported keys"
        )

    def test_a_declaration_that_is_not_a_table_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A bare value must not read as a repository that declared nothing."""
        root = repository(tmp_path / "repo", Checkout())
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool]\nmutation-preflight = "enabled"\n')

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "not as a table" in outcome.findings("manifest"), (
            "the finding must name the non-table declaration"
        )

    def test_an_environment_entry_the_os_cannot_pass_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A key carrying '=' cannot be spawned, so it is refused when declared."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=(
                    "[[checks]]\n"
                    'name = "collect"\n'
                    'command = ["true"]\n'
                    'env = { "APP=MODE" = "test" }\n'
                )
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "checks[0].env" in outcome.findings("manifest"), (
            "the refusal must name the environment table that cannot be passed"
        )
        assert "Traceback" not in outcome.stderr, (
            "an unpassable entry must be a named finding, not an escaping error"
        )

    def test_sandbox_paths_are_refused_on_the_cargo_lane(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """cargo-mutants copies the working tree, so the requirement is vacuous."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest='sandbox_paths = ["README.md"]\n'),
            lane="cargo",
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "cargo-mutants copies the whole working tree" in outcome.findings(
            "manifest"
        ), "the finding must name why the key is refused"


class TestInterpreter:
    """The project interpreter is judged, and both runtimes are reported."""

    @RULE_AVAILABLE
    @pytest.mark.parametrize("interpreter", ["3.13", "3.13.5", "3.14"])
    def test_an_accepted_interpreter_passes(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        interpreter: str,
    ) -> None:
        """A bare minor, a patch and the upper bound's own minor are accepted."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(requires_python=">=3.12,<3.15"),
            project_python=interpreter,
            helper_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "pass", (
            "the interpreter phase must report pass",
            outcome.stdout,
        )

    @RULE_AVAILABLE
    @pytest.mark.parametrize(
        ("requirement", "interpreter"),
        [(">=3.14", "3.13"), ("<3.13", "3.13"), ("==3.12.*", "3.13")],
    )
    def test_a_rejected_interpreter_names_the_range(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        requirement: str,
        interpreter: str,
    ) -> None:
        """A lower bound, an upper bound and another minor are all refused."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(requires_python=requirement),
            project_python=interpreter,
            helper_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "outside the project's requires-python" in outcome.findings(
            "interpreter"
        ), "the finding must name the project's requires-python"
        assert requirement in outcome.findings("interpreter"), (
            "the finding must quote the refused requirement"
        )

    @RULE_AVAILABLE
    def test_the_helper_and_project_runtimes_are_reported_separately(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A helper runtime inside the floor is not evidence about the project."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(requires_python=">=3.14"),
            project_python="3.13",
            helper_python="3.13.5",
        )

        detail = outcome.detail("interpreter")
        assert detail["helper_python"] == "3.13.5", (
            "the helper runtime is reported exactly"
        )
        assert detail["project_python"] == "3.13", (
            "the project runtime is reported exactly"
        )
        assert detail["same_runtime"] is False, (
            "a helper patch release is not the project runtime"
        )
        assert outcome.findings("interpreter").count("outside") == 1, (
            "only the project's range is violated, not the helper's"
        )

    def test_a_helper_runtime_below_its_floor_fails(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The workflow's helper scripts need 3.13 regardless of the project."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(requires_python=">=3.9"),
            project_python="3.12",
            helper_python="3.12.9",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "below the helper floor 3.13" in outcome.findings("interpreter"), (
            "the finding must name the helper floor"
        )

    def test_no_project_interpreter_is_reported_as_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A missing input cannot prove the project refuses its own range."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(),
            project_python="",
            helper_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "unknown", (
            "the interpreter phase must report unknown",
            outcome.stdout,
        )
        assert "not checked" in outcome.findings("interpreter"), (
            "the finding must say the project runtime was not checked"
        )

    def test_a_requires_python_that_is_not_text_still_publishes_its_report(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """TOML can hand back a date, and the report is JSON, not Python."""
        root = repository(tmp_path / "repo", Checkout())
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\nrequires-python = 2026-01-01\n',
            encoding="utf-8",
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "unknown", (
            "the interpreter phase must report unknown",
            outcome.stdout,
        )
        assert "is not a range" in outcome.findings("interpreter"), (
            "the finding must refuse a non-text requires-python"
        )
        report = json.loads(
            (tmp_path / "out" / "mutation-preflight.json").read_text(encoding="utf-8")
        )
        phase = next(item for item in report["phases"] if item["name"] == "interpreter")
        assert phase["detail"]["requires_python"] == "2026-01-01", (
            "the report must publish the refused value verbatim"
        )

    def test_a_rule_that_will_not_load_leaves_support_unknown(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A rule that judged nothing must not fail the interpreter it never read."""
        # Patched where the command line reads it, which is the point the
        # rule enters the run.
        monkeypatch.setattr(
            cli,
            "load_shared_rule",
            lambda: (None, "the shared rule could not be loaded"),
        )

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(requires_python=">=3.12"),
            project_python="3.13",
            helper_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["interpreter"]["status"] == "unknown", (
            "the interpreter phase must report unknown",
            outcome.stdout,
        )
        assert "could not be loaded" in outcome.findings("interpreter"), (
            "the finding must name the rule that would not load"
        )
