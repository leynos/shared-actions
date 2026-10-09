"""Contract tests for Skylos Makefile, configuration, and CI integration.

Makeutil supplies structured Makefile facts so these tests verify variables and
recipes without coupling to source spacing. Runtime tests use an executable
recorder because Make dry runs cannot prove shell argument forwarding.
"""

from __future__ import annotations

import errno
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
import types
import typing as typ
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml
from hypothesis import example, given, settings
from hypothesis import strategies as st
from plumbum import local

from workflow_scripts import skylos_allow

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_GNU_MAKE_INSTALL_TOKENS: typ.Final = (
    "choco",
    "install",
    "make",
    "--no-progress",
    "--yes",
)
_MAKEUTIL_ACTION_REFERENCE: typ.Final = "./.github/actions/install-makeutil-from-source"
_MAKEUTIL_ACTION_INPUTS: typ.Final = {
    "makeutil-revision": "${{ env.MAKEUTIL_REVISION }}",
    "makeutil-toolchain": "${{ env.MAKEUTIL_TOOLCHAIN }}",
}
_MAKEUTIL_ACTION_ENVIRONMENT: typ.Final = {
    "MAKEUTIL_REVISION": "${{ inputs.makeutil-revision }}",
    "MAKEUTIL_TOOLCHAIN": "${{ inputs.makeutil-toolchain }}",
}
_SKYLOS_VERSION_TOKENS: typ.Final = ("4.33.2",)
_SKYLOS_CLI_TOKENS: typ.Final = (
    "$(UV_ENV)",
    "$(UV)",
    "tool",
    "run",
    "--python",
    "3.14",
    "--from",
    "skylos==$(SKYLOS_VERSION)",
    "skylos",
)
_SKYLOS_SCAN_TOKENS: typ.Final = (
    "$(SKYLOS_CLI)",
    "--config-file",
    "pyproject.toml",
)
_SKYLOS_PRODUCTION_TARGET_TOKENS: typ.Final = (
    ".github/actions",
    "workflow_scripts",
    "scripts",
    "actions_common.py",
    "bool_utils.py",
    "cargo_utils.py",
    "cmd_utils.py",
    "cmd_utils_importer.py",
)
_SKYLOS_EXCLUDE_TOKENS: typ.Final = ("tests",)
_SKYLOS_WHITELIST_LOCK_TOKENS: typ.Final = (".skylos-whitelist.lock",)
_UV_SHELL_TOKENS: typ.Final = ("$(subst", ",/,$(UV))")
_WINDOWS_LOCK_NONBLOCKING: typ.Final = 1
_WINDOWS_LOCK_UNLOCK: typ.Final = 2
_TYPECHECK_COMMAND_PREFIX: typ.Final = ("$(UV)", "run", "ty", "check")
_TYPECHECK_FIRST_EXTRA_PATHS: typ.Final = (
    ".",
    ".github/actions/generate-coverage/scripts",
    ".github/actions/ratchet-coverage/scripts",
    ".github/actions/rust-build-release",
    ".github/actions/rust-build-release/src",
    ".github/actions/linux-packages",
    ".github/actions/linux-packages/scripts",
    ".github/actions/windows-package",
    ".github/actions/windows-package/scripts",
    ".github/actions/setup-rust/scripts",
    ".github/actions/install-mdtablefix/tests",
    ".github/actions/install-makeutil/scripts",
    ".github/actions/install-makeutil/tests",
)
_TYPECHECK_FIRST_SOURCE_PATHS: typ.Final = (
    "cmd_utils.py",
    "composite_fragments.py",
    ".github/actions/generate-coverage/scripts",
    ".github/actions/ratchet-coverage/scripts",
    ".github/actions/linux-packages/scripts",
    ".github/actions/rust-build-release/src",
    ".github/actions/setup-rust/scripts",
    ".github/actions/install-mdtablefix/tests",
    ".github/actions/install-makeutil/scripts",
    ".github/actions/install-makeutil/tests",
    ".github/actions/windows-package/scripts",
)
_TYPECHECK_SECOND_EXTRA_PATHS: typ.Final = (
    ".",
    ".github/actions/macos-package/scripts",
)
_TYPECHECK_SECOND_SOURCE_PATHS: typ.Final = (".github/actions/macos-package/scripts",)
_TYPECHECK_FIRST_EXTRA_PATH_TOKENS: typ.Final = tuple(
    token
    for path in _TYPECHECK_FIRST_EXTRA_PATHS
    for token in ("--extra-search-path", path)
)
_TYPECHECK_SECOND_EXTRA_PATH_TOKENS: typ.Final = tuple(
    token
    for path in _TYPECHECK_SECOND_EXTRA_PATHS
    for token in ("--extra-search-path", path)
)
_TYPECHECK_FIRST_RECIPE_TOKENS: typ.Final = (
    _TYPECHECK_COMMAND_PREFIX
    + _TYPECHECK_FIRST_EXTRA_PATH_TOKENS
    + _TYPECHECK_FIRST_SOURCE_PATHS
)
_TYPECHECK_SECOND_RECIPE_TOKENS: typ.Final = (
    _TYPECHECK_COMMAND_PREFIX
    + _TYPECHECK_SECOND_EXTRA_PATH_TOKENS
    + _TYPECHECK_SECOND_SOURCE_PATHS
)
_SKYLOS_LINT_TOKENS: typ.Final = (
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
_SKYLOS_WHITELIST_TOKENS: typ.Final = (
    "$(UV_SHELL)",
    "run",
    "--no-project",
    "python",
    "$(SKYLOS_LOCK_HELPER)",
    "--lock-file",
    "$(SKYLOS_WHITELIST_LOCK)",
    "--",
    "$(SKYLOS_CLI)",
    "whitelist",
    "$${SKYLOS_SYMBOL}",
    "--reason",
    "$${SKYLOS_REASON}",
)
_EXPECTED_SKYLOS_WHITELIST_NAMES: typ.Final = frozenset(
    {
        "JsonValue",
        "_CargoProcCtx",
        "_assert_cargo_streams",
        "_build_cargo_env",
        "_finalize_pump_threads",
        "_handle_cargo_output_event",
        "_kill_cargo_process",
        "_poll_pump_loop_iteration",
        "_pump_cargo_output",
        "_pump_cargo_output_posix",
        "_pump_cargo_output_windows",
        "_raise_cargo_timeout",
        "_read_wait_timeout",
        "_resolve_wait_timeout",
        "_run_cargo",
        "_spawn_cargo",
        "_wait_for_cargo",
        "emit",
        "fail",
        "get_line_coverage_percent_from_cobertura",
        "lines_from_detail",
        "request_graphql",
        "with_env",
    }
)
_EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES: typ.Final = (
    _EXPECTED_SKYLOS_WHITELIST_NAMES
    | frozenset(
        {
            "AUTHOR_PAGE_SIZE",
            "AutomergeConfig",
            "COMMIT_PAGE_SIZE",
            "COMMITS_FRAGMENT",
            "COMMITS_PAGE_QUERY",
            "DEPENDABOT_LOGINS",
            "Decision",
            "DecisionStatus",
            "DISABLE_AUTOMERGE_MUTATION",
            "ENABLE_AUTOMERGE_MUTATION",
            "GraphQLQuery",
            "MAX_COMMIT_PAGES",
            "MERGE_PULL_REQUEST_MUTATION",
            "MergeMethod",
            "MergeStateRetryConfig",
            "MergeStateStatus",
            "MergeableState",
            "PULL_REQUEST_QUERY",
            "PullRequestContext",
            "PullRequestRef",
            "DownloadError",
            "outputs",
            "classify_merge_state",
            "commit_audit_outcome",
            "commit_page",
            "emit_decision",
            "emit_withdrawal_notice",
            "evaluate",
            "fetch_pull_request",
            "foreign_commits",
            "handler_order",
            "judge",
            "merge_state_retry_config",
        }
    )
)
_EXPECTED_SKYLOS_ENTRYPOINT_NAMES: typ.Final = frozenset()
_WILDCARD_SYMBOLS: typ.Final = ("*", "?", "[")
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
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$;|&'\"()]{}!\\`",
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


def _normalize_make_text(value: str) -> str:
    """Normalize Makeutil text before removing Make continuations."""
    return value.replace("\r\n", "\n").replace("\\\n", "")


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
    return tuple(shlex.split(_normalize_make_text(value)))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens from every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(_normalize_make_text(recipe_text)))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _make_command(
    *arguments: str,
    environment: dict[str, str],
    working_directory: Path = _REPOSITORY_ROOT,
) -> tuple[int, str, str]:
    """Run the resolved Make executable with an inherited environment."""
    executable = shutil.which("make")
    assert executable is not None, "Skylos contract tests require make on PATH"
    with local.env(**environment):
        return local[executable]["--no-print-directory", *arguments].run(
            retcode=None, cwd=working_directory
        )


def _skylos_allow_environment(**values: str) -> dict[str, str]:
    """Return a clean Skylos boundary environment, including WSL's ``NAME``."""
    environment = {**os.environ, "NAME": "wsl-hostname"}
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    environment.update(values)
    return environment


def _isolated_skylos_allow_arguments(
    directory: Path, *, skylos_cli: str, uv_launcher: Path
) -> tuple[str, ...]:
    """Build a whitelist command isolated from the repository configuration."""
    return (
        "-f",
        str(_REPOSITORY_ROOT / "Makefile"),
        f"UV={_windows_style_path(uv_launcher)}",
        f"SKYLOS_CLI={skylos_cli}",
        f"SKYLOS_WHITELIST_LOCK={directory / '.skylos-whitelist.lock'}",
        "skylos-allow",
    )


def _python_recorder_cli(module_name: str) -> str:
    """Return a shell-safe Python command for a temporary recorder module."""
    return shlex.join((Path(sys.executable).as_posix(), "-m", module_name))


def _windows_style_path(path: Path) -> str:
    """Return a Windows-separated spelling to exercise Make shell normalization."""
    return str(path).replace("/", "\\")


def _uv_passthrough_launcher(directory: Path) -> Path:
    """Create an executable uv stand-in that launches the requested Python command."""
    launcher = directory / "uv-launcher"
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n\n"
        "arguments = sys.argv[1:]\n"
        "if arguments[:3] != ['run', '--no-project', 'python']:\n"
        "    raise SystemExit('unexpected uv arguments')\n"
        "os.execv(sys.executable, [sys.executable, *arguments[3:]])\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


def _whitelist_process(
    directory: Path,
    *,
    skylos_cli: str,
    uv_launcher: Path,
    symbol: str,
    reason: str,
) -> subprocess.Popen[str]:
    """Start one isolated documented-whitelist update."""
    executable = shutil.which("make")
    assert executable is not None, "Skylos contract tests require make on PATH"
    return subprocess.Popen(  # noqa: S603 - fixed Makefile and test arguments.
        [
            executable,
            "--no-print-directory",
            *_isolated_skylos_allow_arguments(
                directory, skylos_cli=skylos_cli, uv_launcher=uv_launcher
            ),
        ],
        cwd=directory,
        env=_skylos_allow_environment(SYMBOL=symbol, REASON=reason),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return a named workflow job."""
    workflow = yaml.safe_load((_REPOSITORY_ROOT / workflow_path).read_text())
    jobs = _mapping(
        _mapping(workflow, subject=f"{workflow_path} workflow").get("jobs"),
        subject=f"{workflow_path} jobs",
    )
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} {job_name} job")


def _makeutil_action() -> dict[str, object]:
    """Return the local Makeutil composite-action manifest."""
    return _mapping(
        yaml.safe_load(
            (
                _REPOSITORY_ROOT
                / ".github/actions/install-makeutil-from-source/action.yml"
            ).read_text()
        ),
        subject="Makeutil composite action",
    )


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
        tuple(shlex.split(_normalize_make_text(command))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must install the pinned Makeutil revision and toolchain"


def _assert_gnu_make_installation(command: object, *, contract: str) -> None:
    """Assert that a Windows workflow command provisions GNU Make."""
    assert isinstance(command, str), f"{contract} must provide a shell command"
    assert tuple(shlex.split(command)) == _GNU_MAKE_INSTALL_TOKENS, (
        f"{contract} must install GNU Make through Chocolatey"
    )


class TestSkylosLintContract:
    """Contract tests for the Skylos lint gate and supporting CI plumbing."""

    def test_skylos_lint_contract_uses_python_314_and_production_scope(
        self,
    ) -> None:
        """The lint target must run Skylos strictly against production modules only."""
        test_prerequisites = _text_sequence(
            _sole_recipe_rule("test").get("prerequisites"),
            subject="test target prerequisites",
        )
        assert "makeutil" in test_prerequisites, (
            "make test must require makeutil before contract tests execute"
        )
        assert _variable_tokens("SKYLOS_VERSION") == _SKYLOS_VERSION_TOKENS, (
            "Skylos version must remain pinned to 4.33.2"
        )
        assert _variable_tokens("SKYLOS_CLI") == _SKYLOS_CLI_TOKENS, (
            "Skylos CLI must use Python 3.14 before its pinned tool source"
        )
        assert _variable_tokens("SKYLOS") == _SKYLOS_SCAN_TOKENS, (
            "Skylos scan options must remain separate from the command-only CLI"
        )
        assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == (
            _SKYLOS_PRODUCTION_TARGET_TOKENS
        ), "Skylos must scan only the reviewed production module set"
        assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == _SKYLOS_EXCLUDE_TOKENS, (
            "Skylos must exclude test-only callers from its production scan"
        )
        commands = [
            command
            for command in _recipe_tokens("lint")
            if command[:1] == ("$(SKYLOS)",)
        ]
        assert commands == [_SKYLOS_LINT_TOKENS], (
            "make lint must run the strict production dead-code gate exactly once"
        )

    def test_skylos_configuration_is_strict_and_documents_every_exception(
        self,
    ) -> None:
        """The Skylos configuration must keep strict mode and reasons aligned."""
        with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
            configuration = tomllib.load(configuration_file)
        tool = _mapping(configuration.get("tool"), subject="tool configuration")
        skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
        gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
        assert gate.get("strict") is True, "Skylos strict gate mode must remain enabled"
        whitelist = _mapping(skylos.get("whitelist"), subject="Skylos whitelist")
        names = frozenset(
            _text_sequence(whitelist.get("names"), subject="whitelist names")
        )
        documented = _mapping(
            whitelist.get("documented"), subject="documented whitelist reasons"
        )
        assert names == _EXPECTED_SKYLOS_WHITELIST_NAMES, (
            "Skylos whitelist names must preserve the consciously reviewed set"
        )
        documented_names = frozenset(documented)
        assert documented_names == _EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES, (
            "Skylos documented whitelist names must preserve the reviewed set"
        )
        assert names <= documented_names, (
            "every legacy Skylos whitelist name must have a documented reason"
        )
        assert all(
            isinstance(reason, str) and reason.strip() for reason in documented.values()
        ), "every Skylos allow-list reason must contain verified runtime-caller text"
        dead_code = _mapping(
            skylos.get("dead_code", {}), subject="Skylos dead-code configuration"
        )
        entrypoints = _objects(
            dead_code.get("entrypoints", []), subject="Skylos dead-code entry points"
        )
        entrypoint_names = frozenset(
            name
            for entrypoint in entrypoints
            for name in _text_sequence(
                entrypoint.get("full_name"), subject="Skylos entry-point names"
            )
        )
        assert entrypoint_names == _EXPECTED_SKYLOS_ENTRYPOINT_NAMES, (
            "Skylos entry-point names must preserve the consciously reviewed set"
        )

    def test_skylos_allow_recipe_dispatches_the_whitelist_subcommand_first(
        self,
    ) -> None:
        """The exception target must not place scan options before ``whitelist``."""
        assert _variable_tokens("UV_SHELL") == _UV_SHELL_TOKENS, (
            "the skylos-allow launcher must normalize Windows executable paths "
            "for the POSIX shell"
        )
        assert (
            _variable_tokens("SKYLOS_WHITELIST_LOCK") == _SKYLOS_WHITELIST_LOCK_TOKENS
        ), "Skylos whitelist updates must use the repository-local lock path"
        commands = [
            command
            for command in _recipe_tokens("skylos-allow")
            if command[:4] == _SKYLOS_WHITELIST_TOKENS[:4]
        ]
        assert commands == [_SKYLOS_WHITELIST_TOKENS], (
            "skylos-allow must lock, then dispatch whitelist before the symbol "
            "and --reason"
        )

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

    @settings(max_examples=25, deadline=None)
    @given(value=st.text(alphabet=" \t", min_size=1, max_size=8))
    def test_skylos_allow_rejects_missing_or_whitespace_values(
        self, value: str
    ) -> None:
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

    def test_skylos_allow_rejects_wildcard_symbols(self) -> None:
        """Wildcard-bearing symbols must not reach the Skylos whitelist command."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        for wildcard in _WILDCARD_SYMBOLS:
            returncode, _stdout, stderr = _make_command(
                "skylos-allow",
                environment=_skylos_allow_environment(
                    SYMBOL=f"registered{wildcard}handler",
                    REASON="verified runtime caller",
                ),
            )
            assert returncode == 2, "skylos-allow must reject wildcard-bearing symbols"
            assert (
                "Error: SYMBOL must not contain wildcard characters (*, ?, or [)"
                in stderr
            ), "skylos-allow must explain its wildcard-symbol rejection"
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "wildcard-symbol validation must not mutate pyproject.toml"
        )

    @settings(max_examples=25, deadline=None)
    @example(symbol="$(handler);!", reason='Loaded "$plugin" | registry')
    @given(symbol=_SHELL_ARGUMENT_TEXT, reason=_SHELL_ARGUMENT_TEXT)
    def test_skylos_allow_forwards_generated_arguments_exactly(
        self, symbol: str, reason: str
    ) -> None:
        """A recorder must receive every valid symbol and reason as one argument."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            recorded_arguments = directory / "arguments.json"
            recorder = directory / "skylos_recorder.py"
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
            uv_launcher = _uv_passthrough_launcher(directory)
            environment = _skylos_allow_environment(
                SKYLOS_ARGUMENTS_PATH=str(recorded_arguments),
                SYMBOL=symbol,
                REASON=reason,
            )
            returncode, _stdout, stderr = _make_command(
                *_isolated_skylos_allow_arguments(
                    directory,
                    skylos_cli=_python_recorder_cli("skylos_recorder"),
                    uv_launcher=uv_launcher,
                ),
                environment=environment,
                working_directory=directory,
            )
            assert returncode == 0, (
                f"skylos-allow must forward valid generated arguments: {stderr}"
            )
            assert recorded_arguments.is_file(), (
                "the temporary Skylos recorder must execute on each supported host; "
                f"stdout={_stdout!r}, stderr={stderr!r}"
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

    def test_skylos_allow_lock_preserves_concurrent_documented_entries(
        self,
    ) -> None:
        """The whitelist lock must prevent concurrent documented-entry loss."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "pyproject.toml").write_text(
                "[tool.skylos.whitelist.documented]\n", encoding="utf-8"
            )
            writer = directory / "skylos_whitelist_writer.py"
            writer.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                "import sys\n"
                "import time\n"
                "symbol = sys.argv[2]\n"
                "reason = sys.argv[4]\n"
                "path = Path('pyproject.toml')\n"
                "contents = path.read_text(encoding='utf-8')\n"
                "time.sleep(0.2)\n"
                "path.write_text(contents + f'{symbol} = {reason!r}\\n', "
                "encoding='utf-8')\n",
                encoding="utf-8",
            )
            writer.chmod(0o755)
            uv_launcher = _uv_passthrough_launcher(directory)
            first = _whitelist_process(
                directory,
                skylos_cli=_python_recorder_cli("skylos_whitelist_writer"),
                uv_launcher=uv_launcher,
                symbol="first",
                reason="first reason",
            )
            second = _whitelist_process(
                directory,
                skylos_cli=_python_recorder_cli("skylos_whitelist_writer"),
                uv_launcher=uv_launcher,
                symbol="second",
                reason="second reason",
            )
            first_stdout, first_stderr = first.communicate()
            second_stdout, second_stderr = second.communicate()

            assert first.returncode == 0, (
                "the first Skylos whitelist update must succeed: "
                f"{first_stdout}{first_stderr}"
            )
            assert second.returncode == 0, (
                "the second Skylos whitelist update must succeed: "
                f"{second_stdout}{second_stderr}"
            )
            with (directory / "pyproject.toml").open("rb") as configuration_file:
                configuration = tomllib.load(configuration_file)
            documented = typ.cast(
                "dict[str, object]",
                configuration["tool"]["skylos"]["whitelist"]["documented"],
            )
        assert documented == {"first": "first reason", "second": "second reason"}, (
            "Skylos whitelist locking must preserve every concurrent documented entry"
        )
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "isolated concurrent Skylos whitelist tests must not mutate pyproject.toml"
        )

    def test_windows_file_lock_retries_and_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Windows byte-range lock retries contention and unlocks on exit."""
        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = _WINDOWS_LOCK_NONBLOCKING
        fake_msvcrt.LK_UNLCK = _WINDOWS_LOCK_UNLOCK
        calls: list[int] = []
        attempts = 0

        def locking(_file_descriptor: int, mode: int, byte_count: int) -> None:
            nonlocal attempts
            assert byte_count == 1, "Windows Skylos locks must cover exactly one byte"
            calls.append(mode)
            if mode == fake_msvcrt.LK_NBLCK:
                attempts += 1
                if attempts == 1:
                    raise OSError(errno.EACCES, "lock is held")

        fake_msvcrt.locking = locking
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        monkeypatch.setattr(
            skylos_allow, "time", types.SimpleNamespace(sleep=lambda _delay: None)
        )
        lock_path = tmp_path / "skylos-whitelist.lock"
        with (
            lock_path.open("a+b") as lock_file,
            skylos_allow._exclusive_windows_file_lock(lock_file),
        ):
            assert lock_path.stat().st_size == 1, (
                "Windows byte-range locks need a persistent lock byte"
            )

        assert calls == [
            _WINDOWS_LOCK_NONBLOCKING,
            _WINDOWS_LOCK_NONBLOCKING,
            _WINDOWS_LOCK_UNLOCK,
        ], "Windows Skylos locks must retry contention before unlocking"
        assert lock_path.read_bytes() == b"\0", (
            "releasing a Windows lock must leave its coordination byte intact"
        )

    def test_windows_file_lock_reraises_non_contention_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only Windows lock-contention errors may be retried."""
        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = _WINDOWS_LOCK_NONBLOCKING

        def locking(_file_descriptor: int, _mode: int, _byte_count: int) -> None:
            raise OSError(errno.EBADF, "invalid lock descriptor")

        fake_msvcrt.locking = locking
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        lock_path = tmp_path / "skylos-whitelist.lock"
        with (
            lock_path.open("a+b") as lock_file,
            pytest.raises(OSError, match="invalid lock descriptor"),
        ):
            skylos_allow._try_acquire_windows_lock(lock_file)

    def test_file_lock_dispatches_to_windows_strategy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Platform dispatch must select the Windows lock without host flock."""
        sentinel = object()
        selected: list[object] = []

        def windows_lock(lock_file: object) -> nullcontext[object]:
            selected.append(lock_file)
            return nullcontext()

        monkeypatch.setattr(skylos_allow, "os", types.SimpleNamespace(name="nt"))
        monkeypatch.setattr(skylos_allow, "_exclusive_windows_file_lock", windows_lock)

        with skylos_allow._exclusive_file_lock(sentinel):
            pass
        assert selected == [sentinel], (
            "Windows lock dispatch must select the Windows advisory lock"
        )

    def test_missing_skylos_executable_reports_status_127(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing configured Skylos executable must return a useful diagnostic."""
        missing_command = f"{tmp_path}/missing-skylos-cli"
        result = skylos_allow.main(
            ["--lock-file", str(tmp_path / "skylos.lock"), "--", missing_command]
        )

        assert result == 127, (
            "a missing Skylos CLI must return command-not-found status"
        )
        assert "cannot execute" in capsys.readouterr().err, (
            "a missing Skylos CLI must identify the failed command"
        )

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
