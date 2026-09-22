"""Behavioural contracts for CodeScene coverage check mode."""

from __future__ import annotations

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
        assert "steps.gate-applicability.outputs.skip != 'true'" in condition, step[
            "name"
        ]


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
    assert "inputs.access-token != ''" in str(upload["if"])
    assert "inputs.access-token != ''" in str(check["if"])
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
        assert "inputs.mode != 'install'" in str(step["if"])


def _triggers(path: Path) -> dict[str, object]:
    """Return a workflow's triggers, read under both spellings of ``on:``."""
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
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

    def test_it_installs_the_pinned_cli_on_a_cold_runner(self) -> None:
        """The install path is what a pull request is here to exercise."""
        workflow = yaml.safe_load(COLD_RUNNER_YML.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["cold-runner-contract"]["steps"]
        installs = [
            step
            for step in steps
            if "upload-codescene-coverage" in str(step.get("uses", ""))
            and str((step.get("with") or {}).get("mode", "")) == "install"
        ]
        assert len(installs) == 1, (
            f"{COLD_RUNNER_YML.name} must install the pinned CLI exactly once; "
            f"found {len(installs)}"
        )
        assert any(
            "cs-coverage was unexpectedly preinstalled" in str(step.get("run", ""))
            for step in steps
        ), f"{COLD_RUNNER_YML.name} no longer proves the runner starts cold"

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
        """A deleted fixture would make the parser proof loop over nothing."""
        text = COLD_RUNNER_YML.read_text(encoding="utf-8")
        assert "no Slipcover cobertura fixtures remain for the parser proof" in text, (
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
        """
        workflow = yaml.safe_load(PARSER_PROOF_YML.read_text(encoding="utf-8"))
        guard = str(workflow["jobs"]["parser-proof"].get("if", ""))

        assert "github.ref == 'refs/heads/main'" in guard, (
            f"the credentialed job is not bound to the trunk ref: {guard!r}"
        )

    def test_it_rejects_the_known_parser_failure(self) -> None:
        """A parse break must fail the proof rather than be tolerated."""
        workflow = PARSER_PROOF_YML.read_text(encoding="utf-8")

        tolerated = "! grep -F 'No matching field found"
        assert tolerated not in workflow, (
            f"{PARSER_PROOF_YML.name} negates the parser-failure grep, which "
            f"would turn the known break into a pass: {tolerated!r}"
        )
        required = {
            "shallow fetch of the merge base": "git fetch --no-tags --depth=1",
            "unshallow fallback": "git fetch --no-tags --unshallow",
            "merge-base check": (
                'git merge-base "origin/$DEFAULT_BRANCH" HEAD >/dev/null'
            ),
            "parser-failure detection": (
                "if grep -F 'No matching field found: close for class "
                "java.io.InputStreamReader'"
            ),
            "parser-failure message": (
                "cs-coverage 1.0.101 reported the known parser failure"
            ),
            "pipeline status capture": "status=${PIPESTATUS[0]}",
            "missing-PASS message": "cs-coverage did not report a PASS result",
        }
        missing = [
            what for what, fragment in required.items() if fragment not in workflow
        ]
        assert not missing, f"{PARSER_PROOF_YML.name} is missing its {missing}"
