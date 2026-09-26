"""Behavioural contracts for CodeScene coverage check mode."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"
#: The offline half of the uploader's contract, on every pull request.
COLD_RUNNER_YML = (
    ACTION_YML.parents[3] / ".github/workflows/test-upload-codescene-coverage.yml"
)
#: The service-touching half, dispatch and trunk only.
PARSER_PROOF_YML = (
    ACTION_YML.parents[3] / ".github/workflows/test-codescene-parser-proof.yml"
)
#: The workflow contracts' one parsing boundary. It refuses a key declared
#: twice and names the file on any failure; loaded by path because this test
#: tree is not a package that can import it.
_WORKFLOW_YAML_SPEC = importlib.util.spec_from_file_location(
    "workflow_yaml", ACTION_YML.parents[3] / "tests/workflows/workflow_yaml.py"
)
assert _WORKFLOW_YAML_SPEC is not None
assert _WORKFLOW_YAML_SPEC.loader is not None
workflow_yaml = importlib.util.module_from_spec(_WORKFLOW_YAML_SPEC)
_WORKFLOW_YAML_SPEC.loader.exec_module(workflow_yaml)
#: The contracts' guard reader: a guard is the conjunction of its top-level
#: ``&&`` terms, and one with an unquoted ``||`` requires nothing. Loaded by
#: path for the same reason as ``workflow_yaml``.
_WORKFLOW_EXPRESSIONS_SPEC = importlib.util.spec_from_file_location(
    "workflow_expressions",
    ACTION_YML.parents[3] / "tests/workflows/workflow_expressions.py",
)
assert _WORKFLOW_EXPRESSIONS_SPEC is not None
assert _WORKFLOW_EXPRESSIONS_SPEC.loader is not None
workflow_expressions = importlib.util.module_from_spec(_WORKFLOW_EXPRESSIONS_SPEC)
_WORKFLOW_EXPRESSIONS_SPEC.loader.exec_module(workflow_expressions)


def _workflow(path: Path) -> dict[object, object]:
    """Return the workflow at *path*, parsed through the shared boundary."""
    return workflow_yaml.load_workflow(path)


def _steps() -> list[dict[str, object]]:
    """Return the composite action steps."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _gate_applicability_step() -> dict[str, object]:
    """Return the check-mode gate-applicability step."""
    return next(step for step in _steps() if step.get("id") == "gate-applicability")


def _validation_step() -> dict[str, object]:
    """Return the action's caller-input validation step."""
    return next(step for step in _steps() if step.get("name") == "Validate inputs")


def _run_applicability_check(
    tmp_path: Path,
    *,
    base_ref: str,
    default_branch: str,
) -> subprocess.CompletedProcess[str]:
    """Execute the gate-applicability shell fragment."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    step = _gate_applicability_step()
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = os.environ | {
        "BASE_REF": base_ref,
        "DEFAULT_BRANCH": default_branch,
        "GITHUB_OUTPUT": str(output),
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def _run_gate_check(
    tmp_path: Path,
    *,
    exit_status: int = 2,
) -> subprocess.CompletedProcess[str]:
    """Execute the gate shell fragment with a configurable CLI stub."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")

    step = next(
        step
        for step in _steps()
        if step.get("name") == "Check coverage against CodeScene gates"
    )
    script = str(step["run"])

    (tmp_path / "coverage.xml").write_text("<coverage/>\n", encoding="utf-8")
    cli = tmp_path / "cs-coverage"
    stderr_diagnostic = (
        "printf 'detailed gate stderr diagnostic\\n' >&2\n" if exit_status else ""
    )
    cli.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'arguments: %s\\n' \"$*\"\n"
        "printf 'detailed gate diagnostic\\n'\n"
        f"{stderr_diagnostic}"
        f"exit {exit_status}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    env = os.environ | {
        "GITHUB_BASE_REF": "main",
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "COVERAGE_FILE": "coverage.xml",
        "INPUT_FORMAT": "cobertura",
    }
    return subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", script],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(
    ("base_ref", "default_branch"),
    [("feature-base", "main"), ("topic", "trunk")],
)
def test_stacked_pull_request_skips_gate_with_warning(
    tmp_path: Path,
    base_ref: str,
    default_branch: str,
) -> None:
    """A non-default pull request base is explicitly skipped."""
    result = _run_applicability_check(
        tmp_path,
        base_ref=base_ref,
        default_branch=default_branch,
    )

    assert result.returncode == 0
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == "skip=true\n"
    assert "::warning title=CodeScene coverage gate skipped::" in result.stdout
    assert base_ref in result.stdout
    assert default_branch in result.stdout


@pytest.mark.parametrize(
    ("base_ref", "default_branch"),
    [("", "main"), ("main", "main"), ("trunk", "trunk")],
)
def test_non_stacked_context_remains_applicable(
    tmp_path: Path,
    base_ref: str,
    default_branch: str,
) -> None:
    """A non-PR or default-branch context continues to the CodeScene gate."""
    result = _run_applicability_check(
        tmp_path,
        base_ref=base_ref,
        default_branch=default_branch,
    )

    assert result.returncode == 0
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == ""
    assert "::warning" not in result.stdout


def test_gate_applicability_runs_only_in_check_mode() -> None:
    """Upload mode cannot enter the check-mode applicability boundary."""
    assert _gate_applicability_step()["if"] == "inputs.mode == 'check'"


def test_skipped_gate_suppresses_all_following_steps() -> None:
    """Every step after the applicability decision honours its skip output."""
    steps = _steps()
    applicability_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("id") == "gate-applicability"
    )

    for step in steps[applicability_index + 1 :]:
        condition = str(step.get("if", ""))
        assert workflow_expressions.requires_every(
            condition, ["steps.gate-applicability.outputs.skip != 'true'"]
        ), f"{step['name']} does not require the skip output: {condition!r}"


def test_gate_success_streams_diagnostic_without_verbose(
    tmp_path: Path,
) -> None:
    """A successful CLI check streams diagnostics without leaking headers."""
    result = _run_gate_check(tmp_path, exit_status=0)

    assert result.returncode == 0
    assert "arguments: check --coverage-files coverage.xml" in result.stdout
    assert "detailed gate diagnostic" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("exit_status", [1, 2, 7])
def test_gate_failure_streams_diagnostic_and_preserves_status(
    tmp_path: Path,
    exit_status: int,
) -> None:
    """A failed CLI check streams details and retains its return code."""
    result = _run_gate_check(tmp_path, exit_status=exit_status)

    assert result.returncode == exit_status
    assert "arguments: check --coverage-files coverage.xml" in result.stdout
    assert "detailed gate diagnostic" in result.stdout
    assert "detailed gate stderr diagnostic" in result.stderr
    hint = "pull request base 'main' must have coverage uploaded"
    if exit_status == 2:
        assert hint in result.stderr
    else:
        assert hint not in result.stderr


def test_shell_steps_bind_caller_inputs_through_environment() -> None:
    """Caller-controlled values never become shell source in a run fragment."""
    for step in _steps():
        run = str(step.get("run", ""))
        assert "${{ inputs." not in run, step["name"]


def test_cli_steps_scope_inputs_without_github_environment_exports() -> None:
    """Caller values stay in individual process environments, never GITHUB_ENV."""
    action = ACTION_YML.read_text(encoding="utf-8")
    upload = next(
        step for step in _steps() if step["name"] == "Upload coverage to CodeScene"
    )
    check = next(
        step
        for step in _steps()
        if step["name"] == "Check coverage against CodeScene gates"
    )
    upload_env = upload["env"]
    check_env = check["env"]
    input_key = "access" + "-" + "token"

    assert "GITHUB_ENV" not in action
    assert isinstance(upload_env, dict)
    assert isinstance(check_env, dict)
    assert str(upload_env["CS_ACCESS_TOKEN"]).endswith(input_key + " }}")
    assert str(check_env["CS_ACCESS_TOKEN"]).endswith(input_key + " }}")
    assert check_env["CS_PROJECT_URL"] == "${{ inputs.project-url }}"
    for step in (upload, check):
        assert workflow_expressions.requires_every(
            str(step["if"]), ["inputs.access-token != ''"]
        ), f"{step['name']} does not require a token: {step['if']!r}"
    manifest = yaml.safe_load(action)
    assert manifest["inputs"]["access-token"] == {
        "description": "CodeScene project access token",
        "required": False,
        "default": "",
    }


def test_malicious_input_is_not_executed_before_validation(tmp_path: Path) -> None:
    """Shell metacharacters remain data when input validation rejects them."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    marker = tmp_path / "executed"
    result = subprocess.run(  # noqa: S603,TID251 - execute the action's bash.
        [bash, "-c", str(_validation_step()["run"])],
        check=False,
        capture_output=True,
        env=os.environ
        | {
            "INPUT_FORMAT": f"$(touch {marker})",
            "INPUT_MODE": "install",
            "INPUT_PROJECT_URL": "",
        },
        text=True,
    )

    assert result.returncode == 1
    assert not marker.exists()


def test_legacy_installer_checksum_fails_closed(tmp_path: Path) -> None:
    """The retired installer checksum cannot authorize a CLI installation."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(_validation_step()["run"])],
        check=False,
        capture_output=True,
        env=os.environ
        | {
            "INPUT_FORMAT": "cobertura",
            "INPUT_MODE": "install",
            "INPUT_PROJECT_URL": "",
            "INPUT_INSTALLER_CHECKSUM": "legacy-checksum",
        },
        text=True,
    )

    assert result.returncode == 1
    assert "installer-checksum is deprecated" in result.stderr
    assert "archive-checksum" in result.stderr


def test_newline_token_remains_one_environment_value(tmp_path: Path) -> None:
    """A newline token cannot add GITHUB_ENV records or become shell syntax."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    coverage = tmp_path / "coverage.xml"
    coverage.write_text("<coverage/>\n", encoding="utf-8")
    marker = tmp_path / "executed"
    captured = tmp_path / "token"
    token = f"line-one\n$(touch {marker})"
    cli = tmp_path / "cs-coverage"
    cli.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\' "$CS_ACCESS_TOKEN" > "$TOKEN_CAPTURE"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    step = next(
        step
        for step in _steps()
        if step.get("name") == "Check coverage against CodeScene gates"
    )
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ
        | {
            "COVERAGE_FILE": coverage.name,
            "INPUT_FORMAT": "cobertura",
            "CS_ACCESS_TOKEN": token,
            "CS_PROJECT_URL": "https://example.invalid/project",
            "TOKEN_CAPTURE": str(captured),
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
    )

    assert result.returncode == 0
    assert captured.read_text(encoding="utf-8") == token
    assert not marker.exists()


def test_install_mode_skips_coverage_file_and_artefact_work() -> None:
    """Install mode does not derive or upload a coverage report."""
    steps = _steps()
    for name in ("Determine coverage file", "Upload coverage GitHub artefact"):
        step = next(step for step in steps if step["name"] == name)
        assert workflow_expressions.requires_every(
            str(step["if"]), ["inputs.mode != 'install'"]
        ), f"{name} runs in install mode: {step['if']!r}"


#: The cold-runner job's guard, whitespace collapsed.
COLD_RUNNER_JOB_GUARD = (
    "github.event_name == 'workflow_dispatch' || "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "github.actor != 'dependabot[bot]'"
)
#: The commands that prove the runner starts without the CLI.
COLD_CHECK_SCRIPT = (
    "if command -v cs-coverage >/dev/null 2>&1; then",
    "echo 'cs-coverage was unexpectedly preinstalled on this runner' >&2",
    "exit 1",
    "fi",
)
#: The commands that fail the lane when the parser proof's fixtures vanish.
FIXTURE_CHECK_SCRIPT = (
    "set -euo pipefail",
    "shopt -s nullglob",
    (
        "fixtures=(.github/actions/upload-codescene-coverage/tests/fixtures/"
        "slipcover-*-cobertura.xml)"
    ),
    'if [ "${#fixtures[@]}" -eq 0 ]; then',
    "echo 'no Slipcover cobertura fixtures remain for the parser proof' >&2",
    "exit 1",
    "fi",
    'for fixture in "${fixtures[@]}"; do',
    (
        "python3 -c 'import sys, xml.etree.ElementTree as ET; "
        'ET.parse(sys.argv[1])\' "$fixture"'
    ),
    "done",
)


def _script(run: object) -> tuple[str, ...]:
    """Return a ``run:`` body's command lines, without blanks or comments."""
    lines = (line.strip() for line in str(run).splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def _cold_runner_steps() -> list[dict[str, object]]:
    """Return the cold-runner job's steps."""
    return _workflow(COLD_RUNNER_YML)["jobs"]["cold-runner-contract"]["steps"]


def _sole_script_steps(
    steps: list[dict[str, object]], script: tuple[str, ...]
) -> list[int]:
    """Return the indices of unguarded steps whose whole body is *script*.

    ``false && <command>`` contains the command's text and runs nothing, and
    so does a step whose ``if:`` is never true, so a substring cannot say a
    command runs.
    """
    return [
        index
        for index, step in enumerate(steps)
        if "if" not in step and _script(step.get("run", "")) == script
    ]


def _triggers(path: Path) -> dict[str, object]:
    """Return a workflow's triggers, read under both spellings of ``on:``."""
    workflow = _workflow(path)
    return workflow.get("on", workflow.get(True))


class TestTheColdRunnerProofRunsOnPullRequests:
    """The offline half. It contacts nothing, so it stays on the lane.

    A pull request that changes the uploader, its CLI manifest or the pinned
    version is the one that needs this proof, so it must be able to start it.
    """

    def test_a_pull_request_can_start_it(self) -> None:
        """The proof that costs nothing must not drift behind a dispatch."""
        triggers = _triggers(COLD_RUNNER_YML)
        assert "pull_request" in triggers, (
            f"{COLD_RUNNER_YML.name} must be startable by a pull request; "
            f"read {triggers!r}"
        )

    def test_its_job_runs_for_a_same_repository_pull_request(self) -> None:
        """Every step below is vacuous if the job itself never starts.

        The guard is compared whole: it is a disjunction by design, so no
        conjunct reading applies, and ``false`` or an extra ``&& false``
        must not pass.
        """
        job = _workflow(COLD_RUNNER_YML)["jobs"]["cold-runner-contract"]
        guard = " ".join(str(job.get("if", "")).split())

        assert guard == COLD_RUNNER_JOB_GUARD, (
            f"{COLD_RUNNER_YML.name} changed when its job runs: {guard!r}"
        )

    def test_it_installs_the_pinned_cli_on_a_cold_runner(self) -> None:
        """The install path is what a pull request is here to exercise.

        The cold check must be a whole unguarded step, and it must run before
        the install: after it, the check proves nothing about the runner.
        """
        steps = _cold_runner_steps()
        installs = [
            index
            for index, step in enumerate(steps)
            if step.get("uses") == "./.github/actions/upload-codescene-coverage"
            and (step.get("with") or {}).get("mode") == "install"
            and "if" not in step
        ]
        cold_checks = _sole_script_steps(steps, COLD_CHECK_SCRIPT)

        assert len(installs) == 1, (
            f"{COLD_RUNNER_YML.name} must install the pinned CLI exactly once, "
            f"unguarded; found {len(installs)}"
        )
        assert len(cold_checks) == 1, (
            f"{COLD_RUNNER_YML.name} no longer proves the runner starts cold"
        )
        assert cold_checks[0] < installs[0], (
            f"{COLD_RUNNER_YML.name} checks for a preinstalled CLI after installing one"
        )

    def test_it_holds_no_credential_and_calls_no_service_command(self) -> None:
        """Its whole reason for staying on the lane is that it contacts nothing.

        The scan is raw text, so it sees comments too. That is stricter than
        the workflow contract, which reads the parse because a comment
        contacts nothing. Here the strictness is free and worth having: a
        file on the pull-request lane has no reason to name the gate
        subcommand even in prose, and the raw reading cannot be fooled by a
        construction the parse walk has not met.
        """
        text = COLD_RUNNER_YML.read_text(encoding="utf-8")
        offences = [
            marker
            for marker in (
                "secrets.CS_ACCESS_TOKEN",
                "cs-coverage check",
                "cs-coverage upload",
            )
            if marker in text
        ]
        assert not offences, (
            f"{COLD_RUNNER_YML.name} is on the pull-request lane and must not "
            f"carry {offences}"
        )

    def test_it_keeps_the_parser_proof_fixtures_honest(self) -> None:
        """A deleted fixture would make the parser proof loop over nothing.

        The script is required whole, on a step with no ``if:``, so neither
        dropping its ``exit 1`` nor guarding the step off passes.
        """
        steps = _cold_runner_steps()

        assert len(_sole_script_steps(steps, FIXTURE_CHECK_SCRIPT)) == 1, (
            f"{COLD_RUNNER_YML.name} no longer fails when the fixtures vanish"
        )


class TestTheParserProofIsDispatchAndTrunkOnly:
    """The service-touching half: its trigger and its guard, then what it rejects."""

    def test_it_cannot_be_started_by_a_pull_request(self) -> None:
        """It runs ``cs-coverage check``, which reads the project config.

        Under main-owned coverage no workflow a pull request can start may
        hold ``CS_ACCESS_TOKEN`` or contact the service, so this half is
        dispatch-only while the offline half stays on the lane.
        """
        triggers = _triggers(PARSER_PROOF_YML)
        assert triggers == {"workflow_dispatch": None}, (
            f"the parser proof must be dispatch-only; read {triggers!r}"
        )

    def test_it_runs_only_from_the_trunk_ref(self) -> None:
        """The trigger alone does not bound which ref's content runs.

        A dispatch selects its own ref and that ref's workflow content runs
        with the repository secret, so any write-access account could
        otherwise read the credential out of a branch it controls.

        The guard is compared whole, not searched: the ref term is a
        substring of ``github.ref == 'refs/heads/main' || true``, which
        binds nothing.
        """
        workflow = _workflow(PARSER_PROOF_YML)
        guard = " ".join(str(workflow["jobs"]["parser-proof"].get("if", "")).split())

        assert guard == "github.ref == 'refs/heads/main'", (
            f"the credentialed job is not bound to the trunk ref alone: {guard!r}"
        )

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                'git fetch --no-tags --unshallow "https://github.com/$REPOSITORY.git"'
                ' "refs/heads/$DEFAULT_BRANCH:refs/remotes/origin/$DEFAULT_BRANCH"',
                id="fetch-default-branch",
            ),
            pytest.param(
                'git merge-base "origin/$DEFAULT_BRANCH" HEAD', id="merge-base"
            ),
            pytest.param(
                "python3 .github/actions/upload-codescene-coverage/scripts/"
                "prove_parser.py",
                id="parse-the-fixtures",
            ),
        ],
    )
    def test_each_proof_command_is_a_steps_sole_command(self, command: str) -> None:
        """A command is required as a whole step, not as text inside one.

        ``false && git fetch ...`` contains the fetch command's text and runs
        nothing, and so does a step whose ``if:`` is never true. Each command
        must therefore be some step's entire ``run:``, on a step with no
        ``if:``. The parse loop's verdicts live in ``prove_parser.py``, where
        ``test_prove_parser.py`` drives them.
        """
        steps = _workflow(PARSER_PROOF_YML)["jobs"]["parser-proof"]["steps"]
        sole = [
            step
            for step in steps
            if str(step.get("run", "")).strip() == command and "if" not in step
        ]
        assert len(sole) == 1, (
            f"{PARSER_PROOF_YML.name} must run {command!r} as one step's sole "
            f"command with no if:; read {[s.get('run') for s in steps]!r}"
        )
