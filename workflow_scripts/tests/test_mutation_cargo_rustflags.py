"""Observe literal Rustflags replacement at the real Cargo child boundary."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import typing as typ

import pytest
import yaml
from plumbum import local

from workflow_scripts import mutation_cargo_command as command
from workflow_scripts import mutation_run_cargo as runner
from workflow_scripts.tests.mutation_cargo_fixture import install_recording_cargo

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/mutation-cargo.yml"
FORWARD = "${{ inputs.rustflags }}"
if typ.TYPE_CHECKING:
    from plumbum.commands.base import BaseCommand
    from plumbum.machines.local import LocalMachine

OBSERVED = (
    "RUSTFLAGS",
    "CARGO_ENCODED_RUSTFLAGS",
    "RUSTC_WRAPPER",
    "RUSTDOC",
    "CARGO_TARGET_DIR",
    "CARGO_HOME",
    "RUSTUP_TOOLCHAIN",
    "UNRELATED",
)


@dataclasses.dataclass(frozen=True)
class _Child:
    """Paths for a fresh recording-child fixture."""

    root: pathlib.Path
    bin_dir: pathlib.Path

    def run(
        self,
        values: dict[str, str],
        *,
        as_module: bool = False,
        arguments: tuple[str, ...] = (),
    ) -> tuple[int, str, str]:
        """Launch the actual helper with a complete, explicit child environment."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("INPUT_") and key not in OBSERVED
        }
        environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + environment.get("PATH", ""),
                "PYTHONPATH": str(ROOT),
                "RECORD": str(self.root / "record.json"),
                "FAKE_CARGO_EXIT": "0",
            }
        )
        environment.update(values)
        entry = (
            ["-m", "workflow_scripts.mutation_run_cargo"]
            if as_module
            else [str(ROOT / "workflow_scripts/mutation_run_cargo.py")]
        )
        with subprocess.Popen(  # noqa: S603 - fixed Python helper and authored test argv.
            [sys.executable, *entry, *arguments],
            env=environment,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            stdout, stderr = process.communicate()
            return process.returncode, stdout, stderr

    def record(self) -> dict[str, object]:
        """Read only the fake Cargo's safe observation fields."""
        return _mapping(json.loads((self.root / "record.json").read_text()))


@pytest.fixture
def child(tmp_path: pathlib.Path) -> _Child:
    """Install a recording Cargo that never compiles or contacts the network."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_recording_cargo(bin_dir, OBSERVED)
    return _Child(tmp_path, bin_dir)


def _mapping(value: object) -> dict[str, object]:
    """Narrow an external JSON or YAML mapping after checking its shape."""
    assert isinstance(value, dict), "JSON or YAML payload must be a mapping"
    assert all(isinstance(key, str) for key in value), (
        "Mapping keys must all be strings"
    )
    return typ.cast("dict[str, object]", value)


class TestChildFlags:
    """Replacement is opt-in and reaches the actual child, without shell parsing."""

    @pytest.mark.parametrize("replacement", [None, ""])
    def test_default_preserves_both_flag_variables(
        self, child: _Child, replacement: str | None
    ) -> None:
        """Existing encoded flags retain their value and precedence by default."""
        values = {
            "RUSTFLAGS": "-C opt-level=2",
            "CARGO_ENCODED_RUSTFLAGS": "-C\x1fdebuginfo=1",
        }
        if replacement is not None:
            values["INPUT_RUSTFLAGS"] = replacement
        code, stdout, stderr = child.run(values)
        assert code == 0, stderr
        assert "mutation_cargo_rustflags_mode=inherited\n" in stdout, (
            "Default flag inheritance must be observable without printing flag values"
        )
        assert values["RUSTFLAGS"] not in stdout + stderr, (
            "Inherited flag contents must not leak into runner events"
        )
        assert values["CARGO_ENCODED_RUSTFLAGS"] not in stdout + stderr, (
            "Inherited encoded flag contents must not leak into runner events"
        )
        assert child.record()["env"] == {
            key: value for key, value in values.items() if key in OBSERVED
        }, "An omitted or empty replacement must preserve both inherited flag variables"

    @pytest.mark.parametrize("encoded", [None, "", "-Z\x1fcodegen-backend=cranelift"])
    @pytest.mark.parametrize(
        "replacement",
        [
            "-D warnings",
            "-D warnings -C opt-level=2",
            "   ",
            "'; $(touch sentinel) `touch sentinel` $",
        ],
    )
    def test_selected_override_removes_encoded_and_preserves_other_environment(
        self, child: _Child, encoded: str | None, replacement: str
    ) -> None:
        """Encoded removal is observable even when its inherited value was empty."""
        preserved = child.root / "preserved"
        values = {
            "INPUT_RUSTFLAGS": replacement,
            "RUSTFLAGS": "-Z old",
            "RUSTC_WRAPPER": str(preserved / "wrapper"),
            "RUSTDOC": str(preserved / "rustdoc"),
            "CARGO_TARGET_DIR": str(preserved / "target"),
            "CARGO_HOME": str(preserved / "cache"),
            "RUSTUP_TOOLCHAIN": "preserved-toolchain",
            "UNRELATED": "preserved",
        }
        if encoded is not None:
            values["CARGO_ENCODED_RUSTFLAGS"] = encoded
        code, stdout, stderr = child.run(values)
        assert "mutation_cargo_rustflags_mode=override\n" in stdout, (
            "A literal replacement must report only the selected mode"
        )
        assert replacement not in stdout + stderr, (
            "Replacement flag contents must not leak into runner events"
        )
        assert code == 0, "Literal override must complete successfully"
        expected = {key: value for key, value in values.items() if key in OBSERVED}
        expected.pop("CARGO_ENCODED_RUSTFLAGS", None)
        expected["RUSTFLAGS"] = replacement
        assert child.record()["env"] == expected, (
            "Override must remove encoded flags and preserve all unrelated "
            "child environment values"
        )
        assert not (child.root / "sentinel").exists(), (
            "Literal flags must never execute shell substitution"
        )

    @pytest.mark.parametrize("replacement", ["-D warnings\n-C opt-level=2", "x\ry"])
    def test_multiline_input_fails_before_cargo(
        self, child: _Child, replacement: str
    ) -> None:
        """An invalid environment value produces a useful diagnostic without a run."""
        code, stdout, stderr = child.run({"INPUT_RUSTFLAGS": replacement})
        assert "mutation_cargo_rustflags_mode=invalid\n" in stdout, (
            "Rejected flags must report invalid mode"
        )
        assert replacement not in stdout + stderr, (
            "Rejected flag contents must not leak into runner events"
        )
        assert code == 1, "Multiline flags must fail with the input-error exit code"
        assert "rustflags must be a single line without CR, LF or NUL" in stderr, (
            "Invalid flags must explain the single-line constraint"
        )
        assert not (child.root / "record.json").exists(), (
            "Invalid flags must be rejected before Cargo is launched"
        )

    def test_nul_is_rejected_before_tool_resolution(self) -> None:
        """NUL cannot cross an OS environment boundary, so validate it directly."""
        with pytest.raises(ValueError, match="single line without CR, LF or NUL"):
            command.cargo_command(local, ["mutants"], "x\0y")


class TestInvocationCompatibility:
    """The flag boundary leaves argument order, entrypoints and reporting intact."""

    @pytest.mark.parametrize("as_module", [False, True])
    @pytest.mark.parametrize(
        ("tool_exit", "expected"), [(0, 0), (2, 0), (3, 0), (1, 1), (4, 4), (70, 70)]
    )
    def test_arguments_and_exit_classification(
        self, child: _Child, *, as_module: bool, tool_exit: int, expected: int
    ) -> None:
        """Both entrypoints preserve the full Cargo argv and informative outcomes."""
        values = {
            "INPUT_RUSTFLAGS": "-D warnings",
            "INPUT_DIR": "crate",
            "INPUT_FILES": "src/a.rs src/b.rs",
            "INPUT_SHARD": "1",
            "INPUT_SHARD_COUNT": "3",
            "INPUT_TIMEOUT_MULTIPLIER": "4",
            "INPUT_EXCLUDE_GLOBS": "a.rs, b.rs",
            "INPUT_EXTRA_ARGS": '--all-features --output "report dir"',
            "FAKE_CARGO_EXIT": str(tool_exit),
        }
        code, stdout, _ = child.run(values, as_module=as_module)
        assert code == expected, (
            "Cargo exit classification must preserve the informational outcome contract"
        )
        assert child.record()["argv"] == [
            "mutants",
            "--in-place",
            "--timeout-multiplier",
            "4",
            "--dir",
            "crate",
            "--shard",
            "1/3",
            "--file",
            "src/a.rs",
            "--file",
            "src/b.rs",
            "--exclude",
            "a.rs",
            "--exclude",
            "b.rs",
            "--all-features",
            "--output",
            "report dir",
        ], "Cargo must receive the complete ordered invocation arguments"
        assert f"mutation_cargo_exit_code={tool_exit}" in stdout, (
            "The existing exit-code event must remain observable"
        )
        assert "mutation_cargo_outcome=" in stdout, (
            "The existing outcome event must remain observable"
        )

    @pytest.mark.parametrize("as_module", [False, True])
    def test_flat_cli_overrides_grouped_environment(
        self, child: _Child, *, as_module: bool
    ) -> None:
        """Grouping target fields preserves the flat CLI and its precedence."""
        values = {
            "INPUT_FILES": "src/env.rs",
            "INPUT_DIR": "env-crate",
            "INPUT_SHARD": "0",
            "INPUT_SHARD_COUNT": "1",
            "INPUT_TIMEOUT_MULTIPLIER": "3",
            "INPUT_EXCLUDE_GLOBS": "env.rs",
            "INPUT_EXTRA_ARGS": "--all-features",
            "INPUT_RUSTFLAGS": "-Z old",
            "INPUT_ALLOW_NO_MUTANTS": "true",
            "CARGO_ENCODED_RUSTFLAGS": "-Z\x1fcodegen-backend=cranelift",
        }
        arguments = (
            "--files=src/cli.rs",
            "--target-dir=cli-crate",
            "--shard=2",
            "--shard-count=4",
            "--timeout-multiplier=5",
            "--exclude-globs=cli.rs",
            "--extra-args=--no-shuffle",
            "--rustflags=-D warnings -C opt-level=2",
            "--allow-no-mutants=false",
        )
        code, _, _ = child.run(values, as_module=as_module, arguments=arguments)
        assert code == 0, (
            "Flat CLI arguments must override grouped environment defaults"
        )
        assert child.record()["argv"] == [
            "mutants",
            "--in-place",
            "--timeout-multiplier",
            "5",
            "--dir",
            "cli-crate",
            "--shard",
            "2/4",
            "--file",
            "src/cli.rs",
            "--exclude",
            "cli.rs",
            "--no-shuffle",
        ], "Flat CLI options must retain Cargo argument ordering"
        assert child.record()["env"] == {"RUSTFLAGS": "-D warnings -C opt-level=2"}, (
            "CLI replacement must reach the child literally and remove encoded flags"
        )

    def test_runner_preserves_the_injected_command_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The extracted builder still receives the runner facade's provider."""
        providers: list[object] = []
        injected = object()

        def recording_builder(
            provider: LocalMachine, arguments: list[str], rustflags: str
        ) -> BaseCommand:
            providers.append(provider)
            assert arguments == [
                "mutants",
                "--in-place",
                "--timeout-multiplier",
                "3",
            ], "The command builder must receive the existing default Cargo arguments"
            assert rustflags == "-D warnings", (
                "The runner must forward the replacement unchanged"
            )
            return local["true"]

        def ignore_report(**_kwargs: object) -> None:
            """Leave outcome reporting to its existing compatibility tests."""

        # Patch only module attributes, never process environment or local.env.
        monkeypatch.setattr(runner, "local", injected)
        monkeypatch.setattr(runner, "cargo_command", recording_builder)
        monkeypatch.setattr(runner, "report_outcome", ignore_report)
        runner.main(rustflags="-D warnings")
        assert providers == [injected], (
            "The command builder must retain the runner-injected provider"
        )


def _read_workflow() -> dict[str, object]:
    """Normalize PyYAML's YAML 1.1 boolean interpretation of the on key."""
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "The workflow document must parse as a mapping"
    if True in parsed:
        parsed["on"] = parsed.pop(True)
    return _mapping(parsed)


def _assert_workflow_contract(workflow: dict[str, object]) -> None:
    """Check the declared input and its sole binding to the real helper step."""
    triggers = _mapping(workflow["on"])
    inputs = _mapping(_mapping(triggers["workflow_call"])["inputs"])
    declared = _mapping(inputs["rustflags"])
    assert declared["type"] == "string", (
        "Rustflags must remain an optional string input"
    )
    assert declared["default"] == "", "Rustflags must preserve the empty default"
    jobs = _mapping(workflow["jobs"])
    bindings = []
    for job in jobs.values():
        steps = _mapping(job).get("steps", [])
        assert isinstance(steps, list), "Workflow job steps must be a list"
        for raw in steps:
            step = _mapping(raw)
            run = step.get("run", "")
            assert isinstance(run, str), "Workflow run bodies must be strings"
            assert FORWARD not in run, (
                "Rustflags expressions must never be interpolated into shell text"
            )
            environment = _mapping(step.get("env", {}))
            if "INPUT_RUSTFLAGS" in environment:
                assert step.get("name") == "Run mutation testing", (
                    "Only the mutation helper step may receive INPUT_RUSTFLAGS"
                )
                assert "mutation_run_cargo.py" in run, (
                    "The binding must target the actual Cargo mutation helper"
                )
                bindings.append(environment["INPUT_RUSTFLAGS"])
            assert FORWARD not in _mapping(step.get("with", {})).values(), (
                "The replacement must not be forwarded through action inputs"
            )
    assert bindings == [FORWARD], (
        "Exactly one mutation helper binding must forward the Rustflags input"
    )


def _mutation_environments(workflow: dict[str, object]) -> list[dict[str, object]]:
    """Find mutable environment mappings for every mutation helper step."""
    environments: list[dict[str, object]] = []
    for job in _mapping(workflow["jobs"]).values():
        steps = _mapping(job).get("steps", [])
        assert isinstance(steps, list), "Workflow job steps must be a list"
        for raw in steps:
            step = _mapping(raw)
            if step.get("name") == "Run mutation testing":
                environments.append(_mapping(step["env"]))
    return environments


class TestWorkflowCarrier:
    """Parsed workflow data carries the replacement without shell interpolation."""

    @pytest.mark.skipif(
        not WORKFLOW.exists(), reason="workflow absent in mutation sandbox"
    )
    def test_declares_and_forwards_the_input(self) -> None:
        """The optional string reaches only the step invoking the helper."""
        _assert_workflow_contract(_read_workflow())

    @pytest.mark.parametrize(
        "replacement", [None, "-D warnings", "${{ inputs.extra-args }}"]
    )
    @pytest.mark.skipif(
        not WORKFLOW.exists(), reason="workflow absent in mutation sandbox"
    )
    def test_workflow_binding_mutations_are_detected(
        self, replacement: str | None
    ) -> None:
        """The same contract rejects missing, literal and wrong-input bindings."""
        workflow = copy.deepcopy(_read_workflow())
        for environment in _mutation_environments(workflow):
            if replacement is None:
                del environment["INPUT_RUSTFLAGS"]
            else:
                environment["INPUT_RUSTFLAGS"] = replacement
        with pytest.raises(AssertionError):
            _assert_workflow_contract(workflow)
