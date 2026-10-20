"""Facts and inspectors shared by the Skylos contract suites.

The Makefile and the CI workflows state the same things twice: once in the
file that runs, and once in a contract that reads it. These helpers read
the first so the suites can assert the second. Makeutil returns the
Makefile as structured facts, so the contracts compare variables and
recipes without coupling to source spacing, and the workflow helpers
return parsed YAML rather than text.

Validation is written as ``raise`` rather than ``assert``: the repository
exempts only ``test_*.py`` files from ``S101``, and this is a support
module. The messages are the same either way, and the failure still
propagates as an ``AssertionError`` so a contract reads the same from
either side.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import yaml
from hypothesis import strategies as st
from plumbum import local

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_REVISION: typ.Final = "7ab9b7b73a8f50ef1d3df548c7def7b94db35ba2"
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
_TYPECHECK_COMMAND_PREFIX: typ.Final = (
    "$(UV)",
    "run",
    "ty",
    "check",
    "--python",
    ".venv",
)
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
    "uv_gate",
    "uv_gate/tests",
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
    "uv_gate",
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
    "--project",
    "$(SKYLOS_LOCK_PROJECT)",
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
_BLANK_TEXT: typ.Final = st.text(alphabet=" \t", min_size=1, max_size=8)
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


def _fail(message: str) -> typ.NoReturn:
    """Raise the contract failure every inspector shares."""
    raise AssertionError(message)


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object while naming an unexpected subject."""
    if not isinstance(value, dict):
        _fail(f"expected {subject} to be a JSON object")
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object array while naming an unexpected subject."""
    if not isinstance(value, list):
        _fail(f"expected {subject} to be a JSON array")
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array while naming an unexpected subject."""
    if not isinstance(value, list):
        _fail(f"expected {subject} to be a JSON array")
    if not all(isinstance(item, str) for item in value):
        _fail(f"expected {subject} to contain only JSON strings")
    return tuple(typ.cast("list[str]", value))


def _normalize_make_text(value: str) -> str:
    """Normalize Makeutil text before removing Make continuations."""
    return value.replace("\r\n", "\n").replace("\\\n", "")


def _makeutil_report() -> dict[str, object]:
    """Return Makeutil's complete Makefile parse report without caching it."""
    executable = shutil.which("makeutil")
    if executable is None:
        _fail("Skylos contract tests require makeutil on PATH")
    returncode, stdout, stderr = local[executable]["parse", "Makefile"].run(
        retcode=None, cwd=_REPOSITORY_ROOT
    )
    if returncode != 0:
        _fail(f"makeutil must parse Makefile successfully: {stderr}")
    report = _mapping(json.loads(stdout), subject="makeutil report")
    parse = _mapping(report.get("parse"), subject="makeutil parse report")
    if parse.get("status") != "complete":
        _fail(f"makeutil must complete the Makefile parse: {parse!r}")
    return report


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makeutil_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    if len(matches) != 1:
        _fail(
            f"expected exactly one Makefile variable named {name!r}, "
            f"found {len(matches)}"
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
    if len(matches) != 1:
        _fail(
            f"expected one recipe-bearing Makefile rule named {target!r}, found "
            f"{len(matches)}"
        )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from Makeutil's raw variable value."""
    value = _sole_variable(name).get("raw_value")
    if not isinstance(value, str):
        _fail(f"expected {name!r} to have a string value")
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
    if executable is None:
        _fail("Skylos contract tests require make on PATH")
    with local.env(**environment):
        return local[executable]["--no-print-directory", *arguments].run(
            retcode=None, cwd=working_directory
        )


def _skylos_allow_environment(**values: str) -> dict[str, str]:
    """Return a clean Skylos boundary environment, including WSL's ``NAME``."""
    environment = dict(os.environ, NAME="wsl-hostname")
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
    """Create an executable uv stand-in that launches the requested Python command.

    The stand-in re-spawns through :mod:`subprocess` rather than ``os.execv``.
    On Windows, ``os.exec*`` joins the argument list into a command line with
    single spaces and no quoting, and the C runtime splits it again on the way
    in, so any argument containing a space or a double quote arrives as several
    arguments. CPython documents the behaviour and declined to change it:
    https://github.com/python/cpython/issues/64650. ``subprocess`` builds the
    Windows command line with ``list2cmdline``, which quotes each element, so
    every argument survives as exactly one argument on every platform.
    """
    launcher = directory / "uv-launcher"
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        f"python = {sys.executable!r}\n"
        f"project = {str(_REPOSITORY_ROOT)!r}\n"
        "arguments = sys.argv[1:]\n"
        "if (len(arguments) < 5 or arguments[:2] != ['run', '--project']\n"
        "        or arguments[3] != 'python'\n"
        "        or Path(arguments[2]).resolve() != Path(project).resolve()):\n"
        "    raise SystemExit('unexpected uv arguments')\n"
        "raise SystemExit(subprocess.call([python, *arguments[4:]]))\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


class _WhitelistRunner(typ.NamedTuple):
    """The fixed boundary one isolated whitelist update runs through.

    Every update in the lock test shares the same directory, recorder
    module, and launcher, so those are the runner and only the symbol and
    reason vary per call.
    """

    directory: Path
    skylos_cli: str
    uv_launcher: Path

    def start(self, symbol: str, reason: str) -> subprocess.Popen[str]:
        """Start one isolated documented-whitelist update."""
        executable = shutil.which("make")
        if executable is None:
            _fail("Skylos contract tests require make on PATH")
        return subprocess.Popen(  # noqa: S603 - fixed Makefile and test arguments.
            [
                executable,
                "--no-print-directory",
                *_isolated_skylos_allow_arguments(
                    self.directory,
                    skylos_cli=self.skylos_cli,
                    uv_launcher=self.uv_launcher,
                ),
            ],
            cwd=self.directory,
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
    if len(matches) != 1:
        _fail(
            f"expected one {step_name!r} step in {workflow_path} {job_name!r}, found "
            f"{len(matches)}"
        )
    return matches[0]


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that a workflow command installs the pinned Makeutil parser."""
    if not isinstance(command, str):
        _fail(f"{contract} must provide a shell command")
    if tuple(shlex.split(_normalize_make_text(command))) != _MAKEUTIL_INSTALL_TOKENS:
        _fail(f"{contract} must install the pinned Makeutil revision and toolchain")


def _assert_gnu_make_installation(command: object, *, contract: str) -> None:
    """Assert that a Windows workflow command provisions GNU Make."""
    if not isinstance(command, str):
        _fail(f"{contract} must provide a shell command")
    if tuple(shlex.split(command)) != _GNU_MAKE_INSTALL_TOKENS:
        _fail(f"{contract} must install GNU Make through Chocolatey")
