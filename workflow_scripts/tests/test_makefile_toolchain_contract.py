"""Contract for the Makefile toolchain the suites and CI depend on.

`make test` runs the contract suites, which read the Makefile through
Makeutil, so the parser and the type checker's search paths are
prerequisites of the gate itself. These tests hold the workflows that
provision the parser and the recipes that invoke the checker to the
versions and paths they declare.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from tempfile import TemporaryDirectory

from workflow_scripts.tests.skylos_contract_support import (
    _MAKEUTIL_ACTION_ENVIRONMENT,
    _MAKEUTIL_ACTION_INPUTS,
    _MAKEUTIL_ACTION_REFERENCE,
    _MAKEUTIL_REVISION,
    _MAKEUTIL_TOOLCHAIN,
    _TYPECHECK_FIRST_RECIPE_TOKENS,
    _TYPECHECK_SECOND_RECIPE_TOKENS,
    _assert_gnu_make_installation,
    _assert_makeutil_installation,
    _make_command,
    _makeutil_action,
    _mapping,
    _normalize_make_text,
    _objects,
    _recipe_tokens,
    _skylos_allow_environment,
    _sole_workflow_step,
    _workflow_job,
)


class TestMakefileToolchainContract:
    """The parser, the checker paths, and their CI provisioning must agree."""

    def test_makeutil_continuations_normalize_crlf_before_tokenizing(self) -> None:
        """CRLF Makefile continuations must not leave carriage-return tokens."""
        parsed_value = "first \\\r\n second"
        normalized_tokens = tuple(shlex.split(_normalize_make_text(parsed_value)))

        assert normalized_tokens == ("first", "second"), (
            "Makeutil tokenization must normalize CRLF before joining continued lines"
        )

    def test_typecheck_recipes_preserve_commands_paths_and_sources(self) -> None:
        """Both Ty invocations must retain their configured search and source paths."""
        recipes = _recipe_tokens("typecheck")
        assert len(recipes) == 2, (
            "typecheck must have exactly two independent Ty recipe invocations"
        )
        assert recipes[0] == _TYPECHECK_FIRST_RECIPE_TOKENS, (
            "the primary typecheck invocation must use UV run ty check with every "
            "required search path and source path"
        )
        assert recipes[1] == _TYPECHECK_SECOND_RECIPE_TOKENS, (
            "the macOS typecheck invocation must use UV run ty check with its "
            "required search path and source path"
        )

    def test_makeutil_target_reports_a_missing_parser_executable(self) -> None:
        """The Makeutil prerequisite must explain how to provision a missing binary."""
        with TemporaryDirectory() as temporary_directory:
            missing_executable = Path(temporary_directory) / "makeutil-not-installed"
            returncode, _stdout, stderr = _make_command(
                f"MAKEUTIL={missing_executable}",
                "makeutil",
                environment=_skylos_allow_environment(),
            )

        assert returncode != 0, (
            "the makeutil target must fail when its configured executable is absent"
        )
        assert (
            "Error: makeutil is required; install the pinned parser documented in "
            "docs/developers-guide.md"
        ) in stderr, "the makeutil target must print its parser installation diagnostic"

    def test_full_suite_workflows_install_the_pinned_makefile_parser(
        self,
    ) -> None:
        """Every isolated full-suite job must provision Makeutil independently."""
        for workflow_path, job_name in (
            (".github/workflows/ci.yml", "python-tests"),
            (".github/workflows/ci.yml", "coverage"),
            (".github/workflows/ci.yml", "python-tests-windows"),
            (".github/workflows/coverage-main.yml", "coverage-upload"),
        ):
            job = _workflow_job(workflow_path, job_name)
            environment = _mapping(
                job.get("env"), subject=f"{workflow_path} {job_name} environment"
            )
            assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
                f"{workflow_path} {job_name} must pin the Makeutil revision"
            )
            assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
                f"{workflow_path} {job_name} must pin the Makeutil nightly toolchain"
            )
            parser_step = _sole_workflow_step(
                workflow_path, job_name, "Install Makefile parser"
            )
            assert parser_step.get("uses") == _MAKEUTIL_ACTION_REFERENCE, (
                f"{workflow_path} {job_name} must use the local Makeutil installer"
            )
            assert (
                _mapping(
                    parser_step.get("with"),
                    subject=f"{workflow_path} {job_name} Makeutil installer inputs",
                )
                == _MAKEUTIL_ACTION_INPUTS
            ), f"{workflow_path} {job_name} must pass both pinned Makeutil inputs"
        action = _makeutil_action()
        inputs = _mapping(action.get("inputs"), subject="Makeutil action inputs")
        for input_name in _MAKEUTIL_ACTION_INPUTS:
            input_configuration = _mapping(
                inputs.get(input_name), subject=f"Makeutil action {input_name} input"
            )
            assert input_configuration.get("required") is True, (
                f"Makeutil action must require {input_name}"
            )
        runs = _mapping(action.get("runs"), subject="Makeutil action runs")
        assert runs.get("using") == "composite", "Makeutil installer must be composite"
        install_steps = _objects(
            runs.get("steps"), subject="Makeutil composite action steps"
        )
        assert len(install_steps) == 1, (
            "Makeutil composite action must have one install step"
        )
        install_step = install_steps[0]
        assert install_step.get("shell") == "bash", (
            "Makeutil installer must preserve its bash execution environment"
        )
        assert (
            _mapping(install_step.get("env"), subject="Makeutil installer environment")
            == _MAKEUTIL_ACTION_ENVIRONMENT
        ), "Makeutil installer must map both caller inputs to its command environment"
        _assert_makeutil_installation(
            install_step.get("run"), contract="Makeutil composite action installation"
        )
        windows_make_step = _sole_workflow_step(
            ".github/workflows/ci.yml", "python-tests-windows", "Install GNU Make"
        )
        _assert_gnu_make_installation(
            windows_make_step.get("run"),
            contract="python-tests-windows GNU Make installation",
        )
