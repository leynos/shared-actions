"""The mutation preflight names what a run would fail on, before it starts.

Each case here is one of the failures issue #591 recorded: a missing README, a
hidden workflow tree, a generator sibling, a Rust toolchain file, a nested
fixture, an interpreter outside the project's range, an absent mold or
mdtablefix. The assertion in each case is that the phase fails *naming the
path, command or boundary*, and the paired case shows the same repository
passing once the requirement is declared or satisfied.
"""

from __future__ import annotations

import typing as typ

import pytest

from workflow_scripts.tests.mutation_preflight_support import (
    RULE_AVAILABLE,
    Checkout,
    Outcome,
    outcome_for,
    repository,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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
        assert outcome.phases["manifest"]["status"] == "skipped"
        assert set(outcome.phases) == {"manifest", "interpreter"}

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

        assert outcome.exit_code == 1
        assert "keep one" in outcome.findings("manifest")
        assert set(outcome.phases) == {"manifest", "interpreter"}, (
            "a refused manifest must not be half-applied to later phases"
        )

    def test_an_unrecognised_key_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A typo would otherwise read as a requirement that is being checked."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest='sandbox_path = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "sandbox_path" in outcome.findings("manifest")
        assert "supported keys" in outcome.findings("manifest")

    def test_a_declaration_that_is_not_a_table_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A bare value must not read as a repository that declared nothing."""
        root = repository(tmp_path / "repo", Checkout())
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool]\nmutation-preflight = "enabled"\n')

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1
        assert "not as a table" in outcome.findings("manifest")

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

        assert outcome.exit_code == 1
        assert "cargo-mutants copies the whole working tree" in outcome.findings(
            "manifest"
        )


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
        assert outcome.phases["interpreter"]["status"] == "pass"

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

        assert outcome.exit_code == 1
        assert "outside the project's requires-python" in outcome.findings(
            "interpreter"
        )
        assert requirement in outcome.findings("interpreter")

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
        assert detail["helper_python"] == "3.13.5"
        assert detail["project_python"] == "3.13"
        assert detail["same_runtime"] is False
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

        assert outcome.exit_code == 1
        assert "below the helper floor 3.13" in outcome.findings("interpreter")

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
        assert outcome.phases["interpreter"]["status"] == "unknown"
        assert "not checked" in outcome.findings("interpreter")


class TestSandbox:
    """Declared paths are checked against what mutmut actually copies."""

    def test_a_missing_readme_is_named(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The README is outside mutmut's copy set and absent, so it is named."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest='sandbox_paths = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "sandbox path 'README.md' is not in the checkout" in outcome.findings(
            "sandbox"
        )

    def test_a_readme_passes_once_also_copy_names_it(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The declared path is satisfied by the copy entry that materialises it."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                also_copy=("conftest.py", "README.md"),
                manifest='sandbox_paths = ["README.md"]\n',
                extra={"README.md": "# example\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "pass"
        assert outcome.detail("sandbox")["not_copied"] == []

    def test_a_hidden_workflow_tree_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A suite that reads .github/workflows needs that tree in mutants/."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = [".github/workflows/ci.yml"]\n',
                extra={".github/workflows/ci.yml": "jobs: {}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert ".github/workflows/ci.yml" in outcome.findings("sandbox")
        assert "also_copy" in outcome.findings("sandbox")

    def test_a_generator_sibling_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A template outside source_paths is not copied just by existing."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = ["templates/report.j2"]\n',
                extra={"templates/report.j2": "{}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert "templates/report.j2" in outcome.findings("sandbox")
        assert outcome.detail("sandbox")["not_copied"] == ["templates/report.j2"]

    @pytest.mark.parametrize(
        ("declared", "contents"),
        [
            ("rust-toolchain.toml", '[toolchain]\nchannel = "stable"\n'),
            ("fixtures/nested/data.json", "{}\n"),
        ],
        ids=["toolchain-file", "nested-fixture"],
    )
    def test_a_file_outside_the_copy_set_is_named(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        contents: str,
    ) -> None:
        """A toolchain file and a nested fixture are requirements like any other.

        Neither sits under ``source_paths``, so existing is not enough: the
        declaration has to be materialised by ``also_copy``.
        """
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=f'sandbox_paths = ["{declared}"]\n',
                extra={declared: contents},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert declared in outcome.findings("sandbox")
        assert outcome.detail("sandbox")["not_copied"] == [declared]

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("/etc/passwd", "is absolute"),
            ("../outside", "escapes the checkout"),
            ("target/debug/example", "build output"),
            (".venv/lib/x.py", "tool cache"),
        ],
    )
    def test_an_unusable_path_is_refused(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        expected: str,
    ) -> None:
        """Absolute, escaping, build-output and cache paths are all refused."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                also_copy=("conftest.py", declared),
                manifest=f'sandbox_paths = ["{declared}"]\n',
                extra={declared.lstrip("./"): "x\n"}
                if not declared.startswith("/")
                else {},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        assert expected in outcome.findings("sandbox")

    def test_a_repository_without_mutmut_configuration_reports_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Without a copy set the comparison cannot be made, and says so."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["conftest.py"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown"
        assert "cannot be established" in outcome.findings("sandbox")

    def test_a_setup_cfg_without_a_mutmut_section_is_unknown_not_a_crash(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """An absent section is unknown configuration, not a lookup failure."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["conftest.py"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )
        (root / "setup.cfg").write_text(
            "[metadata]\nname = example\n", encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown"
        assert "neither pyproject.toml's [tool.mutmut]" in outcome.findings("sandbox")

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("../outside", "escapes the checkout"),
            ("README.md", "is not in the checkout"),
        ],
    )
    def test_a_path_is_decided_though_the_copy_set_is_unknown(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        expected: str,
    ) -> None:
        """Neither question depends on knowing what mutmut would copy."""
        root = repository(
            tmp_path / "repo", Checkout(manifest=f'sandbox_paths = ["{declared}"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1
        assert expected in outcome.findings("sandbox")

    def test_a_prepared_checkout_is_read_as_the_test_wrote_it(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Passing no checkout must not overwrite the one the test prepared."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["README.md"]\n')
        )
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool.mutation-preflight]\nsandbox_paths = ["README.md"]\n')

        outcome = outcome_for(run_preflight, root, project_python="3.13")

        assert outcome.exit_code == 1
        assert "keep one" in outcome.findings("manifest")
