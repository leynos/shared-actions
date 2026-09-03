"""Tests for the whole-workspace coverage inputs.

``all-features``, ``all-targets`` and ``doctests`` exist so one coverage job
can be a repository's only test execution. These tests cover the manifest
contract for the three inputs, the flags they render into the cargo command,
the precedence of ``all-features`` over the narrower feature inputs, and the
separate uninstrumented doc-test run. The environment guarantee that a
caller's ``RUSTFLAGS`` survives into every cargo invocation is checked here
too, because these inputs are the reason a caller would set it.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

import pytest
import typer
import yaml
from plumbum import local

from test_support.cmd_mox_stub_adapter import DefaultResponse
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

    from test_support.cmd_mox_stub_adapter import StubManager

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"
SCRIPTS_DIR = ACTION_DIR / "scripts"

#: Manifest inputs added for whole-workspace runs, mapped to their defaults.
#: Every one defaults to off so existing callers see no change.
WORKSPACE_INPUTS = {
    "all-features": "false",
    "all-targets": "false",
    "doctests": "false",
}

#: Environment variable the Rust coverage step must carry each input in.
INPUT_ENVIRONMENT = {
    "all-features": "INPUT_ALL_FEATURES",
    "all-targets": "INPUT_ALL_TARGETS",
    "doctests": "INPUT_DOCTESTS",
}


def _load_script(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    """Import ``name`` from the action's ``scripts`` directory."""
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[4])
    monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclass field resolution looks the defining
    # module up in ``sys.modules`` while the class body runs.
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_rust(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module."""
    return _load_script(monkeypatch, "run_rust")


@pytest.fixture
def cargo_runner(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``_cargo_runner`` module."""
    return _load_script(monkeypatch, "_cargo_runner")


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed composite action manifest."""
    loaded = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rust_step() -> dict[str, typ.Any]:
    """Return the Rust coverage step."""
    steps = _manifest()["runs"]["steps"]
    matches = [step for step in steps if step.get("id") == "rust"]
    assert len(matches) == 1, "expected exactly one Rust coverage step"
    return matches[0]


@pytest.mark.parametrize(("name", "default"), sorted(WORKSPACE_INPUTS.items()))
def test_workspace_inputs_declared_and_default_off(name: str, default: str) -> None:
    """Each input must exist and leave existing callers unchanged."""
    inputs = _manifest()["inputs"]
    assert name in inputs, f"input {name!r} missing from the manifest"
    assert inputs[name].get("required", False) is False
    assert inputs[name].get("default") == default


@pytest.mark.parametrize(("name", "variable"), sorted(INPUT_ENVIRONMENT.items()))
def test_workspace_inputs_reach_the_coverage_script(name: str, variable: str) -> None:
    """Each input must be forwarded verbatim to the Rust coverage step."""
    environment = _rust_step()["env"]
    assert environment.get(variable) == f"${{{{ inputs.{name} }}}}"


def test_all_targets_and_all_features_render_into_the_cargo_command(
    run_rust: ModuleType,
) -> None:
    """The rendered command must carry both flags when both inputs are set."""
    args = run_rust.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=True,
        all_features=True,
        all_targets=True,
    )

    assert args == [
        "llvm-cov",
        "nextest",
        "--manifest-path",
        "Cargo.toml",
        "--workspace",
        "--all-targets",
        "--all-features",
        "--lcov",
        "--output-path",
        "cov.lcov",
    ]


def test_flags_are_absent_by_default(run_rust: ModuleType) -> None:
    """Neither flag may appear unless the caller asked for it."""
    args = run_rust.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        use_nextest=True,
    )

    assert "--all-features" not in args
    assert "--all-targets" not in args


def test_all_features_supersedes_default_feature_selection(
    run_rust: ModuleType,
) -> None:
    """``--all-features`` must not be paired with ``--no-default-features``."""
    args = run_rust.feature_selection_args("", with_default=False, all_features=True)

    assert args == ["--all-features"]


def test_argument_builder_reports_nothing(
    run_rust: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The builder is a pure query; diagnostics belong to the boundary."""
    run_rust.feature_selection_args("cli", with_default=False, all_features=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@dataclasses.dataclass(frozen=True, slots=True)
class DiagnosticsCase:
    """One feature selection and the diagnostics it should produce.

    ``expected_error`` and ``expected_warning`` hold a substring to look for,
    or ``None`` when that diagnostic must be absent.
    """

    features: str
    with_default: bool
    all_features: bool
    expected_error: str | None = None
    expected_warning: str | None = None


DIAGNOSTICS_CASES = (
    DiagnosticsCase("", with_default=True, all_features=False),
    DiagnosticsCase("cli", with_default=True, all_features=False),
    DiagnosticsCase("", with_default=False, all_features=False),
    DiagnosticsCase("", with_default=True, all_features=True),
    DiagnosticsCase(
        "", with_default=False, all_features=True, expected_warning="supersedes"
    ),
    DiagnosticsCase(
        "cli",
        with_default=True,
        all_features=True,
        expected_error="already enables every feature",
    ),
    DiagnosticsCase(
        "cli",
        with_default=False,
        all_features=True,
        expected_error="already enables every feature",
    ),
)


@pytest.mark.parametrize("case", DIAGNOSTICS_CASES)
def test_feature_selection_diagnostics_are_a_pure_query(
    run_rust: ModuleType, case: DiagnosticsCase
) -> None:
    """Each selection maps to the diagnostics it deserves, with no output."""
    error, warning = run_rust.feature_selection_diagnostics(
        case.features,
        with_default=case.with_default,
        all_features=case.all_features,
    )

    assert (error is None) is (case.expected_error is None)
    assert (warning is None) is (case.expected_warning is None)
    if case.expected_error is not None:
        assert case.expected_error in error
    if case.expected_warning is not None:
        assert case.expected_warning in warning


def test_boundary_check_warns_on_superseded_defaults(
    run_rust: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary reports what the builder silently resolves."""
    run_rust.check_feature_selection("", with_default=False, all_features=True)

    assert "supersedes with-default-features" in capsys.readouterr().err


def test_all_features_with_a_features_list_fails_closed(
    run_rust: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller naming both selections must be told, not silently widened."""
    with pytest.raises(typer.Exit) as excinfo:
        run_rust.check_feature_selection(
            "cli,tui", with_default=True, all_features=True
        )

    assert excinfo.value.exit_code == 1
    assert "already enables every feature" in capsys.readouterr().err


def test_features_list_alone_still_renders(run_rust: ModuleType) -> None:
    """The narrower selection keeps working when all-features is off."""
    args = run_rust.feature_selection_args(
        "cli,tui", with_default=False, all_features=False
    )

    assert args == ["--no-default-features", "--features", "cli,tui"]


def test_doctest_command_omits_all_targets(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doc tests are their own target kind, so ``--all-targets`` cannot apply."""
    recorded: dict[str, typ.Any] = {}

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        recorded["args"] = args
        return ""

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.run_doctests(
        "",
        manifest_path=Path("Cargo.toml"),
        cargo_env={},
        with_default=True,
        all_features=True,
    )

    assert recorded["args"] == [
        "test",
        "--doc",
        "--workspace",
        "--manifest-path",
        "Cargo.toml",
        "--all-features",
    ]


def _run_main_capturing_cargo(
    run_rust: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    doctests: bool,
) -> list[list[str]]:
    """Run ``main`` with cargo stubbed and return every command it issued."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\n")
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append(args)
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.main(
        output,
        "",
        with_default=True,
        use_nextest=True,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=tmp_path / "gh.txt",
        cucumber_rs_features="",
        cucumber_rs_args="",
        with_cucumber_rs=False,
        all_features=False,
        all_targets=False,
        doctests=doctests,
        baseline_file=None,
    )
    return calls


def test_doctests_run_after_the_instrumented_run(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The doc-test run follows the coverage run and is uninstrumented."""
    calls = _run_main_capturing_cargo(run_rust, tmp_path, monkeypatch, doctests=True)

    assert len(calls) == 2
    assert calls[0][0] == "llvm-cov"
    assert calls[1][:3] == ["test", "--doc", "--workspace"]
    assert "llvm-cov" not in calls[1]


def test_doctests_are_skipped_when_not_requested(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that did not ask for doc tests must not pay for them."""
    calls = _run_main_capturing_cargo(run_rust, tmp_path, monkeypatch, doctests=False)

    assert len(calls) == 1


def test_caller_rustflags_survive_into_cargo(
    cargo_runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller's ``RUSTFLAGS`` must reach every cargo invocation.

    The estate runs coverage with warnings denied, so the value the workflow
    exports has to survive the environment the action builds for cargo.
    """
    monkeypatch.setenv("RUSTFLAGS", "-D warnings")

    env = cargo_runner._build_cargo_env(
        {"CARGO_PROFILE_DEV_CODEGEN_BACKEND": "llvm"},
        ("CARGO_PROFILE_TEST_CODEGEN_BACKEND",),
    )

    assert env["RUSTFLAGS"] == "-D warnings"


def test_coverage_environment_never_touches_rustflags(
    run_rust: ModuleType, tmp_path: Path
) -> None:
    """Neither the overrides nor the unsets may name ``RUSTFLAGS``."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[profile.dev]\ncodegen-backend = "cranelift"\n')

    overrides = run_rust.get_cargo_coverage_env(manifest)

    assert "RUSTFLAGS" not in overrides
    assert "RUSTFLAGS" not in run_rust._CARGO_COVERAGE_ENV_UNSETS


def test_main_forwards_the_selection_to_both_commands(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-workspace run must reach the coverage and doc-test commands.

    Both are built from the same selection, so a change that forwarded the
    flags to only one of them would leave the doc tests compiling a different
    feature set from the code they document.
    """
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\n")
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append(args)
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.main(
        output,
        "",
        with_default=True,
        use_nextest=True,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=tmp_path / "gh.txt",
        cucumber_rs_features="",
        cucumber_rs_args="",
        with_cucumber_rs=False,
        all_features=True,
        all_targets=True,
        doctests=True,
        baseline_file=None,
    )

    coverage_args, doctest_args = calls
    assert "--all-targets" in coverage_args
    assert "--all-features" in coverage_args
    assert "--all-features" in doctest_args
    assert "--all-targets" not in doctest_args


def test_main_forwards_a_narrow_selection_to_the_doc_tests(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named feature list and disabled defaults must reach the doc tests."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\n")
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append(args)
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.main(
        output,
        "cli,tui",
        with_default=False,
        use_nextest=True,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=tmp_path / "gh.txt",
        cucumber_rs_features="",
        cucumber_rs_args="",
        with_cucumber_rs=False,
        all_features=False,
        all_targets=False,
        doctests=True,
        baseline_file=None,
    )

    _coverage_args, doctest_args = calls
    assert doctest_args == [
        "test",
        "--doc",
        "--workspace",
        "--manifest-path",
        "Cargo.toml",
        "--no-default-features",
        "--features",
        "cli,tui",
    ]


def test_main_rejects_a_conflicting_selection_before_running_cargo(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conflict must fail the step, not run a widened coverage build."""
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:  # pragma: no cover - must never be reached
        calls.append(args)
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    with pytest.raises(typer.Exit) as excinfo:
        run_rust.main(
            tmp_path / "cov.lcov",
            "cli",
            with_default=True,
            use_nextest=True,
            lang="rust",
            fmt="lcov",
            manifest_path=Path("Cargo.toml"),
            github_output=tmp_path / "gh.txt",
            cucumber_rs_features="",
            cucumber_rs_args="",
            with_cucumber_rs=False,
            all_features=True,
            all_targets=False,
            doctests=False,
            baseline_file=None,
        )

    assert excinfo.value.exit_code == 1
    assert calls == []


def test_run_rust_script_honours_the_input_environment(
    tmp_path: Path, shell_stubs: StubManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the script the way the composite action does.

    The action passes these selections as environment variables, so this runs
    ``run_rust.py`` as a subprocess against a stubbed ``cargo`` and asserts the
    two commands it issues, rather than calling ``main`` directly.
    """
    output = tmp_path / "cov.lcov"
    output.write_text("LF:200\nLH:163\n")
    github_output = tmp_path / "gh.txt"
    shell_stubs.register("cargo", default=DefaultResponse(stdout="Coverage: 81.5%\n"))
    monkeypatch.chdir(tmp_path)

    environment = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(output),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "DETECTED_CARGO_MANIFEST": "Cargo.toml",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_USE_CARGO_NEXTEST": "true",
        "INPUT_ALL_FEATURES": "true",
        "INPUT_ALL_TARGETS": "true",
        "INPUT_DOCTESTS": "true",
        "GITHUB_OUTPUT": str(github_output),
        "RUSTFLAGS": "-D warnings",
    }
    script = ACTION_DIR / "scripts" / "run_rust.py"
    repository_root = Path(__file__).resolve().parents[4]
    merged = {**os.environ, **environment}
    existing_path = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = (
        f"{repository_root}{os.pathsep}{existing_path}"
        if existing_path
        else str(repository_root)
    )
    merged["PYTHONIOENCODING"] = "utf-8"
    returncode, _stdout, stderr = run_plumbum_command(
        local[sys.executable][str(script)], method="run", env=merged
    )

    assert returncode == 0, stderr
    coverage_call, doctest_call = shell_stubs.calls_of("cargo")
    assert coverage_call.argv == [
        "llvm-cov",
        "nextest",
        "--manifest-path",
        "Cargo.toml",
        "--workspace",
        "--all-targets",
        "--all-features",
        "--lcov",
        "--output-path",
        str(output),
    ]
    assert doctest_call.argv == [
        "test",
        "--doc",
        "--workspace",
        "--manifest-path",
        "Cargo.toml",
        "--all-features",
    ]
    assert coverage_call.env["RUSTFLAGS"] == "-D warnings"
    assert doctest_call.env["RUSTFLAGS"] == "-D warnings"


def test_cucumber_command_carries_the_whole_workspace_flags(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cucumber run must measure the same code as the main run.

    ``main`` forwards the selection to ``run_cucumber_rs_coverage`` so the two
    reports can be merged. If the cucumber command built a narrower feature
    set, the merged report would mix two different builds of the workspace.
    """
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\nend_of_record\n")
    cucumber_output = output.with_name(f"{output.stem}.cucumber{output.suffix}")
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append(args)
        if str(cucumber_output) in args:
            cucumber_output.write_text("LF:4\nLH:4\nend_of_record\n")
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.main(
        output,
        "",
        with_default=True,
        use_nextest=True,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=tmp_path / "gh.txt",
        cucumber_rs_features="tests/features",
        cucumber_rs_args="",
        with_cucumber_rs=True,
        all_features=True,
        all_targets=True,
        doctests=False,
        baseline_file=None,
    )

    _coverage_args, cucumber_args = calls
    assert str(cucumber_output) in cucumber_args
    assert "--all-features" in cucumber_args
    assert "--all-targets" in cucumber_args


def test_cucumber_command_omits_the_flags_when_not_requested(
    run_rust: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that asked for neither flag must not get them via cucumber."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\nend_of_record\n")
    cucumber_output = output.with_name(f"{output.stem}.cucumber{output.suffix}")
    calls: list[list[str]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append(args)
        if str(cucumber_output) in args:
            cucumber_output.write_text("LF:4\nLH:4\nend_of_record\n")
        return "Coverage: 100%"

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)

    run_rust.main(
        output,
        "",
        with_default=True,
        use_nextest=True,
        lang="rust",
        fmt="lcov",
        manifest_path=Path("Cargo.toml"),
        github_output=tmp_path / "gh.txt",
        cucumber_rs_features="tests/features",
        cucumber_rs_args="",
        with_cucumber_rs=True,
        all_features=False,
        all_targets=False,
        doctests=False,
        baseline_file=None,
    )

    _coverage_args, cucumber_args = calls
    assert "--all-features" not in cucumber_args
    assert "--all-targets" not in cucumber_args
