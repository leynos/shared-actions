"""Contract tests for Skylos Makefile, configuration, and CI integration.

Makeutil supplies structured Makefile facts so these tests verify variables and
recipes without coupling to source spacing. Runtime tests use an executable
recorder because Make dry runs cannot prove shell argument forwarding.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tomllib
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from hypothesis import example, given, settings
from hypothesis import strategies as st
from plumbum import local

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)
_SHELL_ARGUMENT_TEXT: typ.Final = st.builds(
    lambda prefix, content, suffix: prefix + content + suffix,
    st.text(alphabet=" \t", max_size=4),
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$;|&'\"()[]{}*?!\\`",
        min_size=1,
        max_size=40,
    ),
    st.text(alphabet=" \t", max_size=4),
)


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object while naming an unexpected subject."""
    assert isinstance(value, dict), f"expected {subject} to be a JSON object"
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object array while naming an unexpected subject."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array while naming an unexpected subject."""
    assert isinstance(value, list), f"expected {subject} to be a JSON array"
    assert all(isinstance(item, str) for item in value), (
        f"expected {subject} to contain only JSON strings"
    )
    return tuple(typ.cast("list[str]", value))


def _makeutil_report() -> dict[str, object]:
    """Return Makeutil's complete Makefile parse report without caching it."""
    executable = shutil.which("makeutil")
    assert executable is not None, "Skylos contract tests require makeutil on PATH"
    returncode, stdout, stderr = local[executable]["parse", "Makefile"].run(
        retcode=None, cwd=_REPOSITORY_ROOT
    )
    assert returncode == 0, f"makeutil must parse Makefile successfully: {stderr}"
    report = _mapping(json.loads(stdout), subject="makeutil report")
    parse = _mapping(report.get("parse"), subject="makeutil parse report")
    assert parse.get("status") == "complete", (
        f"makeutil must complete the Makefile parse: {parse!r}"
    )
    return report


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makeutil_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one Makefile variable named {name!r}, found {len(matches)}"
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the only Makeutil rule for ``target`` that has recipes."""
    rules = _objects(_makeutil_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"expected one recipe-bearing Makefile rule named {target!r}, found "
        f"{len(matches)}"
    )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from Makeutil's raw variable value."""
    value = _sole_variable(name).get("raw_value")
    assert isinstance(value, str), f"expected {name!r} to have a string value"
    return tuple(shlex.split(value.replace("\\\n", "")))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens from every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(recipe_text.replace("\\\n", "")))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _make_command(*arguments: str, environment: dict[str, str]) -> tuple[int, str, str]:
    """Run the resolved Make executable with an inherited environment."""
    executable = shutil.which("make")
    assert executable is not None, "Skylos contract tests require make on PATH"
    with local.env(**environment):
        return local[executable]["--no-print-directory", *arguments].run(
            retcode=None, cwd=_REPOSITORY_ROOT
        )


def _skylos_allow_environment(**values: str) -> dict[str, str]:
    """Return a clean Skylos boundary environment, including WSL's ``NAME``."""
    environment = {**os.environ, "NAME": "wsl-hostname"}
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    environment.update(values)
    return environment


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return a named workflow job."""
    workflow = yaml.safe_load((_REPOSITORY_ROOT / workflow_path).read_text())
    jobs = _mapping(
        _mapping(workflow, subject=f"{workflow_path} workflow").get("jobs"),
        subject=f"{workflow_path} jobs",
    )
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} {job_name} job")


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named workflow step for a job."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} {job_name} steps",
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"expected one {step_name!r} step in {workflow_path} {job_name!r}, found "
        f"{len(matches)}"
    )
    return matches[0]


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that a workflow command installs the pinned Makeutil parser."""
    assert isinstance(command, str), f"{contract} must provide a shell command"
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must install the pinned Makeutil revision and toolchain"


def test_skylos_lint_contract_uses_python_314_and_production_scope() -> None:
    """The lint target must run Skylos strictly against production modules only."""
    test_prerequisites = _text_sequence(
        _sole_recipe_rule("test").get("prerequisites"),
        subject="test target prerequisites",
    )
    assert "makeutil" in test_prerequisites, (
        "make test must require makeutil before contract tests execute"
    )
    assert _variable_tokens("SKYLOS_VERSION") == ("4.33.2",), (
        "Skylos version must remain pinned to 4.33.2"
    )
    assert _variable_tokens("SKYLOS_CLI") == (
        "$(UV_ENV)",
        "$(UV)",
        "tool",
        "run",
        "--python",
        "3.14",
        "--from",
        "skylos==$(SKYLOS_VERSION)",
        "skylos",
    ), "Skylos CLI must use Python 3.14 before its pinned tool source"
    assert _variable_tokens("SKYLOS") == (
        "$(SKYLOS_CLI)",
        "--config-file",
        "pyproject.toml",
    ), "Skylos scan options must remain separate from the command-only CLI"
    assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == (
        ".github/actions",
        "workflow_scripts",
        "scripts",
        "actions_common.py",
        "bool_utils.py",
        "cargo_utils.py",
        "cmd_utils.py",
        "cmd_utils_importer.py",
    ), "Skylos must scan only the reviewed production module set"
    assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == ("tests",), (
        "Skylos must exclude test-only callers from its production scan"
    )
    commands = [
        command for command in _recipe_tokens("lint") if command[:1] == ("$(SKYLOS)",)
    ]
    assert commands == [
        (
            "$(SKYLOS)",
            "$(SKYLOS_PRODUCTION_TARGETS)",
            "--exclude",
            "$(SKYLOS_EXCLUDE_FOLDERS)",
            "--category",
            "dead_code",
            "--gate",
            "--format",
            "concise",
            "--no-upload",
            "--no-provenance",
            "--no-grep-verify",
        )
    ], "make lint must run the strict production dead-code gate exactly once"


def test_skylos_configuration_is_strict_and_documents_every_exception() -> None:
    """The Skylos configuration must keep strict mode and reasons aligned."""
    with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)
    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
    gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
    assert gate.get("strict") is True, "Skylos strict gate mode must remain enabled"
    whitelist = _mapping(skylos.get("whitelist"), subject="Skylos whitelist")
    names = frozenset(_text_sequence(whitelist.get("names"), subject="whitelist names"))
    documented = _mapping(
        whitelist.get("documented"), subject="documented whitelist reasons"
    )
    assert names == frozenset(documented), (
        "every Skylos allow-list name must have exactly one documented reason"
    )
    assert all(
        isinstance(reason, str) and reason.strip() for reason in documented.values()
    ), "every Skylos allow-list reason must contain verified runtime-caller text"


def test_skylos_allow_recipe_dispatches_the_whitelist_subcommand_first() -> None:
    """The exception target must not place scan options before ``whitelist``."""
    commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == ("$(SKYLOS_CLI)",)
    ]
    assert commands == [
        (
            "$(SKYLOS_CLI)",
            "whitelist",
            "$${SKYLOS_SYMBOL}",
            "--reason",
            "$${SKYLOS_REASON}",
        )
    ], "skylos-allow must dispatch whitelist before the symbol and --reason"


@settings(max_examples=25, deadline=None)
@given(value=st.text(alphabet=" \t", min_size=1, max_size=8))
def test_skylos_allow_rejects_missing_or_whitespace_values(value: str) -> None:
    """Missing and whitespace-only exception values must exit two without writes."""
    pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
    requests = (
        ({}, "SYMBOL"),
        ({"SYMBOL": "handler"}, "REASON"),
        ({"SYMBOL": value, "REASON": "verified caller"}, "SYMBOL"),
        ({"SYMBOL": "handler", "REASON": value}, "REASON"),
    )
    for values, missing_name in requests:
        returncode, _stdout, stderr = _make_command(
            "skylos-allow", environment=_skylos_allow_environment(**values)
        )
        assert returncode == 2, (
            f"skylos-allow must reject missing or whitespace-only {missing_name}"
        )
        assert (
            f"Error: {missing_name} is required for a named whitelist exception"
            in stderr
        ), f"skylos-allow must name the missing {missing_name} validation error"
    assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
        "invalid skylos-allow requests must not mutate pyproject.toml"
    )


@settings(max_examples=25, deadline=None)
@example(symbol="$(handler);*", reason='Loaded "$plugin" | registry')
@given(symbol=_SHELL_ARGUMENT_TEXT, reason=_SHELL_ARGUMENT_TEXT)
def test_skylos_allow_forwards_generated_arguments_exactly(
    symbol: str, reason: str
) -> None:
    """A recorder must receive every valid symbol and reason as one argument."""
    pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
    with TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        recorded_arguments = directory / "arguments.json"
        recorder = directory / "skylos-recorder"
        recorder.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n\n"
            'Path(os.environ["SKYLOS_ARGUMENTS_PATH"]).write_text(\n'
            "    json.dumps(sys.argv[1:]), encoding='utf-8'\n"
            ")\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)
        environment = _skylos_allow_environment(
            SKYLOS_ARGUMENTS_PATH=str(recorded_arguments),
            SYMBOL=symbol,
            REASON=reason,
        )
        returncode, _stdout, stderr = _make_command(
            f"SKYLOS_CLI={recorder.as_posix()}",
            "skylos-allow",
            environment=environment,
        )
        assert returncode == 0, (
            f"skylos-allow must forward valid generated arguments: {stderr}"
        )
        assert json.loads(recorded_arguments.read_text(encoding="utf-8")) == [
            "whitelist",
            symbol,
            "--reason",
            reason,
        ], "Skylos must receive each generated value as exactly one argument"
    assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
        "recorder-backed skylos-allow requests must not mutate pyproject.toml"
    )


def test_full_suite_workflows_install_the_pinned_makefile_parser() -> None:
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
        _assert_makeutil_installation(
            parser_step.get("run"),
            contract=f"{workflow_path} {job_name} Makeutil installation",
        )
