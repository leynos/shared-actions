"""Tests for the split main-coverage inputs of ``generate-coverage``.

A repository whose main coverage runs on a runner the pull-request lanes cannot
share a cache store with splits the work three ways: the measuring job runs
with ``publish-baseline: never`` and hands its figures on, the
``publish-coverage-baseline`` action ratchets and saves them on a runner that
shares the pull-request store, and a ``build-only`` job warms the compiler
cache. These tests pin the properties that keep that split safe: a lower figure
fails the publishing job and saves nothing, a hand-over exists only from a
successful trunk push, the save is visible to the pull-request restore, and the
build-only job compiles with the same selection and environment the coverage
run uses.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

ACTION_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ACTION_DIR / "scripts"
PUBLISH_DIR = ACTION_DIR.parent / "publish-coverage-baseline"


@pytest.fixture
def run_rust(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module."""
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[4])
    monkeypatch.delitem(sys.modules, "run_rust", raising=False)
    spec = importlib.util.spec_from_file_location(
        "run_rust", SCRIPTS_DIR / "run_rust.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "run_rust", module)
    spec.loader.exec_module(module)
    return module


def _load(path: Path) -> dict[str, typ.Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps(path: Path) -> list[dict[str, typ.Any]]:
    return _load(path)["runs"]["steps"]


def _step(path: Path, name: str) -> dict[str, typ.Any]:
    (match,) = [step for step in _steps(path) if step.get("name") == name]
    return match


def _condition(step: dict[str, typ.Any]) -> str:
    return " ".join(str(step.get("if", "")).split())


ACTION_YML = ACTION_DIR / "action.yml"
PUBLISH_YML = PUBLISH_DIR / "action.yml"


# -- the measuring job ------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Restore baselines", "Ensure baseline files", "Ratchet coverage"]
)
def test_never_skips_every_baseline_read_and_check(name: str) -> None:
    """A check that cannot fail on this runner must not run."""
    assert "inputs.publish-baseline != 'never'" in _condition(_step(ACTION_YML, name))


def test_never_never_saves() -> None:
    """The measuring job's store is not the one pull requests read."""
    condition = _condition(_step(ACTION_YML, "Save baselines"))

    assert "inputs.publish-baseline != 'never'" in condition
    assert "success()" in condition


def test_validator_accepts_never_and_still_refuses_a_typo() -> None:
    """The set of values is closed: a typo must not read as 'not always'."""
    script = _step(ACTION_YML, "Validate baseline publication mode")["run"]
    probe = f"GC_PUBLISH_BASELINE=$1\n{script}"

    def run(value: str) -> int:
        return subprocess.run(  # noqa: S603,TID251 - fixed argv, test-only
            ["bash", "-c", probe, "probe", value],  # noqa: S607
            capture_output=True,
            check=False,
        ).returncode

    assert run("never") == 0
    assert run("auto") == 0
    assert run("always") == 0
    assert run("nver") != 0


def test_handover_exists_only_for_a_successful_trunk_push() -> None:
    """A failed run or a dispatch must never hand a figure on."""
    condition = _condition(_step(ACTION_YML, "Collect baseline hand-over"))

    for term in (
        "success()",
        "inputs.publish-baseline == 'never'",
        "inputs.baseline-artefact-name != ''",
        "github.event_name == 'push'",
        "github.ref == 'refs/heads/main'",
    ):
        assert term in condition, f"hand-over condition omits {term!r}"
    upload = _condition(_step(ACTION_YML, "Upload baseline hand-over"))
    assert "steps.handover.outcome == 'success'" in upload


def test_new_inputs_default_to_the_existing_behaviour() -> None:
    """An existing caller sets neither and must behave as before."""
    inputs = _load(ACTION_YML)["inputs"]

    assert inputs["build-only"]["default"] == "false"
    assert inputs["baseline-artefact-name"]["default"] == ""
    assert inputs["publish-baseline"]["default"] == "auto"


@pytest.mark.parametrize("step_id", ["python", "out"])
def test_build_only_skips_the_report_path(step_id: str) -> None:
    """Build-only produces no report, so nothing reads one."""
    (step,) = [s for s in _steps(ACTION_YML) if s.get("id") == step_id]

    assert "inputs.build-only != 'true'" in _condition(step)


def test_build_only_reaches_the_rust_script() -> None:
    """The Rust step must forward the input verbatim."""
    (rust,) = [s for s in _steps(ACTION_YML) if s.get("id") == "rust"]

    assert rust["env"]["INPUT_BUILD_ONLY"] == "${{ inputs.build-only }}"


# -- the publishing job -----------------------------------------------------


def test_ratchet_runs_before_a_save_that_requires_success() -> None:
    """A lower figure fails the job before the save, so nothing is written."""
    names = [step.get("name") for step in _steps(PUBLISH_YML)]

    assert names.index("Ratchet coverage") < names.index("Save baselines")
    assert "success()" in _condition(_step(PUBLISH_YML, "Save baselines"))
    # No `always()` or `failure()` may widen the save past a failed ratchet.
    assert "always()" not in _condition(_step(PUBLISH_YML, "Save baselines"))


def test_save_is_guarded_on_a_trunk_push() -> None:
    """A dispatch must read the baseline it measures, never replace it."""
    condition = _condition(_step(PUBLISH_YML, "Save baselines"))

    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition


def test_save_is_visible_to_the_pull_request_restore() -> None:
    """The save must match the key, path list and prefix the lanes restore.

    The path list is part of a cache entry's version, so a different list
    would save an entry no restore can find.
    """
    lane = _step(ACTION_YML, "Restore baselines")["with"]
    restore = _step(PUBLISH_YML, "Restore baselines")["with"]
    save = _step(PUBLISH_YML, "Save baselines")["with"]

    segment_in_lane = "${{ steps.interpreter.outputs.baseline-segment }}"
    segment_here = "${{ steps.handover.outputs.segment }}"
    assert lane["path"] == restore["path"] == save["path"]
    assert lane["key"].replace(segment_in_lane, segment_here) == save["key"]
    assert (
        lane["restore-keys"].replace(segment_in_lane, segment_here)
        == (restore["restore-keys"])
    )


def test_a_lower_figure_fails_and_leaves_the_baseline_untouched(
    tmp_path: Path,
) -> None:
    """Run the comparison the publishing job runs: a drop exits non-zero."""
    baseline = tmp_path / ".coverage-baseline.rust"
    baseline.write_text("80.00")
    # The script the publishing job's own step names, resolved from its
    # action directory the way the runner resolves `github.action_path`.
    named = _step(PUBLISH_YML, "Ratchet coverage")["env"]["GC_RATCHET"]
    relative = named.replace("${{ github.action_path }}", "")
    ratchet = (PUBLISH_DIR / relative.lstrip("/")).resolve()
    assert ratchet.is_file()

    result = subprocess.run(  # noqa: S603,TID251 - fixed argv, test-only
        [
            sys.executable,
            str(ratchet),
            "--baseline-file",
            str(baseline),
            "--current",
            "70.00",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert baseline.read_text() == "80.00"


# -- the build-only job -----------------------------------------------------


def _selection(run_rust: ModuleType, **overrides: bool | str) -> object:
    values: dict[str, bool | str] = {
        "features": "",
        "with_default": True,
        "use_nextest": True,
        "all_features": True,
        "all_targets": True,
        "doctests": True,
        "allow_no_tests": False,
        "build_only": True,
    }
    return run_rust.FeatureSelection(**{**values, **overrides})


def test_build_command_compiles_without_running_the_report(
    run_rust: ModuleType,
) -> None:
    """cargo-llvm-cov's own --no-run means report-without-running."""
    args = run_rust.get_build_only_cmd(
        "",
        manifest_path=Path("Cargo.toml"),
        target_dir=Path("target/llvm-cov-target"),
        selection=_selection(run_rust),
    )

    assert args[:3] == ["nextest", "run", "--no-run"]
    assert "llvm-cov" not in args
    assert "--all-targets" in args
    assert "--all-features" in args
    assert args[args.index("--target-dir") + 1] == "target/llvm-cov-target"


def test_build_command_without_nextest_uses_cargo_test(run_rust: ModuleType) -> None:
    """The plain-cargo form compiles the same selection."""
    args = run_rust.get_build_only_cmd(
        "x",
        manifest_path=Path("Cargo.toml"),
        target_dir=Path("t"),
        selection=_selection(
            run_rust, use_nextest=False, all_features=False, with_default=False
        ),
    )

    assert args[:2] == ["test", "--no-run"]
    assert "--no-default-features" in args
    assert args[args.index("--features") + 1] == "x"


def test_show_env_output_is_parsed_through_shell_quoting(run_rust: ModuleType) -> None:
    """Values with spaces or quotes arrive intact; noise lines are ignored."""
    parsed = run_rust.parse_show_env(
        "A='x y'\nnot a pair\nB=plain\n__C='-Ca --cfg=coverage'\n"
    )

    assert parsed == {"A": "x y", "B": "plain", "__C": "-Ca --cfg=coverage"}


def test_build_only_drops_the_variables_a_real_run_never_sets(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stray CARGO_* variable would turn every compiler-cache hit to a miss."""
    printed = (
        "LLVM_PROFILE_FILE='/w/target/p-%p-%32m.profraw'\n"
        "RUSTC_WRAPPER=/bin/cargo-llvm-cov\n"
        "CARGO_LLVM_COV=1\n"
        "CARGO_LLVM_COV_SHOW_ENV=1\n"
        "CARGO_LLVM_COV_TARGET_DIR=/w/target\n"
        "CARGO_LLVM_COV_BUILD_DIR=/w/target\n"
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        calls.append((args, dict(env_overrides or {})))
        return printed

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)
    targets = run_rust.CoverageTargets(
        out=tmp_path / "unused",
        lang="rust",
        fmt="cobertura",
        manifest_path=tmp_path / "Cargo.toml",
        github_output=tmp_path / "gh",
        baseline_file=None,
    )

    run_rust.build_instrumented_tests(targets, _selection(run_rust))

    assert calls[0][0] == ["llvm-cov", "show-env"]
    build_args, env = calls[1]
    assert build_args[:3] == ["nextest", "run", "--no-run"]
    assert env["CARGO_LLVM_COV"] == "1"
    assert env["RUSTC_WRAPPER"] == "/bin/cargo-llvm-cov"
    for leaked in (
        "CARGO_LLVM_COV_SHOW_ENV",
        "CARGO_LLVM_COV_TARGET_DIR",
        "CARGO_LLVM_COV_BUILD_DIR",
    ):
        assert leaked not in env
    target_dir = Path("/w/target/llvm-cov-target")
    assert build_args[build_args.index("--target-dir") + 1] == str(target_dir)
    assert env["LLVM_PROFILE_FILE"] == str(target_dir / "p-%p-%32m.profraw")


def test_main_in_build_only_mode_writes_no_report(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No coverage figure exists, so nothing is reported to GITHUB_OUTPUT."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INPUT_BUILD_ONLY", "true")
    monkeypatch.setattr(run_rust, "_run_cargo", lambda *a, **k: "")
    github_output = tmp_path / "gh.txt"

    run_rust.main(
        tmp_path / "cov.xml",
        "",
        with_default=True,
        use_nextest=True,
        lang="rust",
        fmt="cobertura",
        manifest_path=Path("Cargo.toml"),
        github_output=github_output,
        baseline_file=None,
    )

    assert not github_output.exists()
