"""Contract and property tests for Rust coverage Cargo argument selection."""

from __future__ import annotations

import dataclasses
import shlex
import typing as typ
from pathlib import Path

import pytest
import yaml
from _coverage_test_support import _exit_code
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from script_loader import load_script_module

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType


@pytest.fixture
def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module for testing."""
    return load_script_module(monkeypatch, "run_rust")


_EXTRA_CARGO_ARGS_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
_CARGO_ARG_TOKEN = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_=./ ",
    min_size=1,
    max_size=24,
)


def test_get_cargo_coverage_cmd_inserts_extra_args_before_report_tail(
    run_rust_module: ModuleType,
) -> None:
    """Extra Cargo arguments are inserted after workspace selection."""
    baseline = run_rust_module.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=False,
    )
    with_extra = run_rust_module.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=False,
        extra_cargo_args=["--exclude", "rustc-proxy"],
    )
    assert baseline == [
        "llvm-cov",
        "--manifest-path",
        "Cargo.toml",
        "--workspace",
        "--lcov",
        "--output-path",
        "cov.lcov",
    ]
    assert with_extra == [
        *baseline[:-3],
        "--exclude",
        "rustc-proxy",
        *baseline[-3:],
    ]


@pytest.mark.parametrize(
    "package_args",
    [
        ["--package", "coverage-helper"],
        ["-p", "coverage-helper"],
        ["-pcoverage-helper"],
        ["--package=coverage-helper"],
    ],
)
def test_get_cargo_coverage_cmd_drops_workspace_for_package_selection(
    run_rust_module: ModuleType,
    package_args: list[str],
) -> None:
    """Explicit package selection replaces the all-workspace default."""
    args = run_rust_module.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=False,
        extra_cargo_args=package_args,
    )
    assert "--workspace" not in args
    assert args[3 : 3 + len(package_args)] == package_args


def test_get_cargo_coverage_cmd_keeps_workspace_for_exclusions(
    run_rust_module: ModuleType,
) -> None:
    """Cargo exclusions retain the workspace selector they refine."""
    args = run_rust_module.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=False,
        extra_cargo_args=["--exclude", "rustc-proxy"],
    )
    assert args[3:6] == ["--workspace", "--exclude", "rustc-proxy"]


@_EXTRA_CARGO_ARGS_SETTINGS
@given(tokens=st.lists(_CARGO_ARG_TOKEN, max_size=8))
def test_parse_extra_cargo_args_round_trips_shell_quoted_tokens(
    run_rust_module: ModuleType,
    tokens: list[str],
) -> None:
    """Shell-quoted argument sequences retain every token and its order."""
    assert run_rust_module._parse_extra_cargo_args(shlex.join(tokens)) == tokens


@_EXTRA_CARGO_ARGS_SETTINGS
@given(values=st.lists(_CARGO_ARG_TOKEN, max_size=8))
def test_extra_cargo_args_retain_order_in_primary_command(
    run_rust_module: ModuleType,
    values: list[str],
) -> None:
    """Every parsed token reaches the primary Cargo command in order."""
    tokens = [f"--config={value}" for value in values]
    parsed = run_rust_module._parse_extra_cargo_args(shlex.join(tokens))
    args = run_rust_module.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=False,
        extra_cargo_args=parsed,
    )
    workspace_index = args.index("--workspace")
    format_index = args.index("--lcov")
    assert args[workspace_index + 1 : format_index] == tokens


@_EXTRA_CARGO_ARGS_SETTINGS
@given(fragment=st.text(alphabet="abcdefghijklmnopqrstuvwxyz -_", max_size=40))
def test_parse_extra_cargo_args_rejects_unterminated_quotes(
    run_rust_module: ModuleType,
    fragment: str,
) -> None:
    """Every unmatched double quote is rejected with the canonical diagnostic."""
    with pytest.raises(ValueError, match="Invalid extra-cargo-args value"):
        run_rust_module._parse_extra_cargo_args(f'--package "{fragment}')


def test_parse_extra_cargo_args_rejects_malformed_quoting(
    run_rust_module: ModuleType,
) -> None:
    """Malformed shell quoting is reported as an input validation error."""
    with pytest.raises(ValueError, match="Invalid extra-cargo-args value"):
        run_rust_module._parse_extra_cargo_args('--exclude "unterminated')


def test_main_translates_invalid_extra_cargo_args_into_typer_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_rust_module: ModuleType,
) -> None:
    """Malformed Cargo arguments exit cleanly before Cargo can run."""
    output = tmp_path / "cov.lcov"
    github_output = tmp_path / "gh.txt"

    def fail_if_cargo_runs(*_args: object, **_kwargs: object) -> None:
        pytest.fail("cargo must not run when argument validation fails")

    monkeypatch.setattr(run_rust_module, "_run_cargo", fail_if_cargo_runs)
    with pytest.raises(run_rust_module.typer.Exit) as excinfo:
        run_rust_module.main(
            output,
            "",
            with_default=True,
            use_nextest=False,
            lang="rust",
            fmt="lcov",
            manifest_path=Path("Cargo.toml"),
            github_output=github_output,
            extra_cargo_args='--exclude "unterminated',
            with_cucumber_rs=False,
        )
    assert _exit_code(excinfo.value) == 2
    assert "Invalid extra-cargo-args value" in capsys.readouterr().err


@dataclasses.dataclass(frozen=True, slots=True)
class ExtraCargoArgsPrecedenceCase:
    """CLI value and the Cargo arguments it should produce."""

    cli_value: str | None
    expected: tuple[str, ...]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ExtraCargoArgsPrecedenceCase(cli_value="", expected=()),
            id="explicit-empty-suppresses-env",
        ),
        pytest.param(
            ExtraCargoArgsPrecedenceCase(
                cli_value="--package cli-crate", expected=("--package", "cli-crate")
            ),
            id="explicit-value-wins",
        ),
        pytest.param(
            ExtraCargoArgsPrecedenceCase(
                cli_value=None, expected=("--exclude", "env-crate")
            ),
            id="omitted-uses-env",
        ),
    ],
)
def test_main_extra_cargo_args_cli_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    case: ExtraCargoArgsPrecedenceCase,
) -> None:
    """An explicit CLI value, including an empty one, overrides the environment."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\n", encoding="utf-8")
    github_output = tmp_path / "gh.txt"
    monkeypatch.setenv("INPUT_EXTRA_CARGO_ARGS", "--exclude env-crate")
    recorded: dict[str, object] = {}

    def fake_run_cargo(args: list[str], **_kwargs: object) -> str:
        recorded["args"] = args
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust_module, "_run_cargo", fake_run_cargo)
    run_rust_module.main(
        output,
        "",
        with_default=True,
        use_nextest=False,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=github_output,
        extra_cargo_args=case.cli_value,
        with_cucumber_rs=False,
        baseline_file=None,
    )

    args = typ.cast("list[str]", recorded["args"])
    extra = list(case.expected)
    selects_package = "--package" in extra
    assert args == [
        "llvm-cov",
        "--manifest-path",
        "Cargo.toml",
        *([] if selects_package else ["--workspace"]),
        *extra,
        "--lcov",
        "--output-path",
        str(output),
    ]
    assert ("--workspace" in args) is not selects_package


def test_parse_extra_cargo_args_or_exit_translates_value_error(
    capsys: pytest.CaptureFixture[str],
    run_rust_module: ModuleType,
) -> None:
    """The CLI parser boundary emits stderr and exits with status 2."""
    with pytest.raises(run_rust_module.typer.Exit) as excinfo:
        run_rust_module._parse_extra_cargo_args_or_exit('--exclude "unterminated')
    assert _exit_code(excinfo.value) == 2
    assert "Invalid extra-cargo-args value" in capsys.readouterr().err


@_EXTRA_CARGO_ARGS_SETTINGS
@given(values=st.lists(_CARGO_ARG_TOKEN, max_size=8))
def test_extra_cargo_args_retain_order_in_cucumber_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    values: list[str],
) -> None:
    """Every parsed token reaches the cucumber Cargo command in order."""
    tokens = [f"--config={value}" for value in values]
    out = tmp_path / "cov.lcov"
    cucumber_file = tmp_path / "cov.cucumber.lcov"
    out.write_text("TN:primary\nend_of_record\n", encoding="utf-8")
    cucumber_file.write_text("TN:cucumber\nend_of_record\n", encoding="utf-8")
    cargo_calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        _ = (env_overrides, env_unsets)
        cargo_calls.append(args)
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust_module, "_run_cargo", fake_run_cargo)
    run_rust_module.run_cucumber_rs_coverage(
        out,
        "lcov",
        "",
        manifest_path=Path("Cargo.toml"),
        cargo_env={},
        with_default=True,
        use_nextest=False,
        cucumber_rs_features="tests/features",
        cucumber_rs_args="",
        extra_cargo_args=tokens,
    )
    assert len(cargo_calls) == 1
    args = cargo_calls[0]
    workspace_index = args.index("--workspace")
    format_index = args.index("--lcov")
    assert args[workspace_index + 1 : format_index] == tokens


@dataclasses.dataclass(frozen=True, slots=True)
class OptionalCucumberCoverageCase:
    """Inputs and expectation for optional cucumber coverage gating."""

    enabled: bool
    features_path: str
    expected_calls: int


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            OptionalCucumberCoverageCase(
                enabled=False, features_path="tests/features", expected_calls=0
            ),
            id="disabled",
        ),
        pytest.param(
            OptionalCucumberCoverageCase(
                enabled=True, features_path="", expected_calls=0
            ),
            id="missing-features",
        ),
        pytest.param(
            OptionalCucumberCoverageCase(
                enabled=True, features_path="tests/features", expected_calls=1
            ),
            id="enabled-with-features",
        ),
    ],
)
def test_run_optional_cucumber_coverage_requires_both_inputs(
    monkeypatch: pytest.MonkeyPatch,
    run_rust_module: ModuleType,
    case: OptionalCucumberCoverageCase,
) -> None:
    """Optional cucumber coverage runs only when enabled and configured."""
    calls: list[tuple[object, ...]] = []

    def record_cucumber(*args: object, **kwargs: object) -> None:
        calls.append((*args, kwargs))

    monkeypatch.setattr(run_rust_module, "run_cucumber_rs_coverage", record_cucumber)
    targets = run_rust_module.CoverageTargets(
        out=Path("cov.lcov"),
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=Path("gh.txt"),
        baseline_file=None,
    )
    selection = run_rust_module.FeatureSelection(
        features="",
        with_default=True,
        use_nextest=False,
        all_features=False,
        all_targets=False,
        doctests=False,
        allow_no_tests=False,
        extra_cargo_args=("--exclude", "rustc-proxy"),
    )
    cucumber = run_rust_module.CucumberSelection(
        enabled=case.enabled,
        features=case.features_path,
        args="--tag fast",
    )
    run_rust_module._run_optional_cucumber_coverage(
        targets, selection, cucumber, {"CARGO_PROFILE_DEV_CODEGEN_BACKEND": "llvm"}
    )
    assert len(calls) == case.expected_calls


def test_generate_coverage_extra_cargo_args_action_contract() -> None:
    """The action declares and forwards the extra Cargo argument input."""
    action_path = Path(__file__).resolve().parents[1] / "action.yml"
    with action_path.open(encoding="utf-8") as stream:
        action = yaml.safe_load(stream)
    assert isinstance(action, dict)
    inputs = action.get("inputs")
    assert isinstance(inputs, dict)
    assert "extra-cargo-args" in inputs
    assert inputs["extra-cargo-args"]["required"] is False

    runs = action.get("runs")
    assert isinstance(runs, dict)
    steps = runs.get("steps")
    assert isinstance(steps, list)
    rust_step = next(
        (step for step in steps if isinstance(step, dict) and step.get("id") == "rust"),
        None,
    )
    assert rust_step is not None, "Missing generate-coverage Rust step"
    env = rust_step.get("env")
    assert isinstance(env, dict)
    assert env["INPUT_EXTRA_CARGO_ARGS"] == "${{ inputs.extra-cargo-args }}"
