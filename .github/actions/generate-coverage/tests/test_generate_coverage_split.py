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
import os
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
    """Parse a YAML file, asserting that its top level is a mapping."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps(path: Path) -> list[dict[str, typ.Any]]:
    """Return the ``runs.steps`` list from an action YAML file."""
    return _load(path)["runs"]["steps"]


def _step(path: Path, name: str) -> dict[str, typ.Any]:
    """Return the single step with the given ``name``.

    Raises ``ValueError`` from the unpacking if zero or several steps match.
    """
    (match,) = [step for step in _steps(path) if step.get("name") == name]
    return match


def _condition(step: dict[str, typ.Any]) -> str:
    """Return the step's ``if`` expression with whitespace collapsed.

    A step without an ``if`` key yields an empty string.
    """
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
        """Run the validator script with ``value`` and return its exit status."""
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


@pytest.mark.parametrize(
    ("current", "fails"),
    [("70.00", True), ("79.00", False), ("78.99", True)],
)
def test_a_figure_beyond_the_tolerance_fails_and_leaves_the_baseline_alone(
    tmp_path: Path, current: str, *, fails: bool
) -> None:
    """Run the comparison the publishing job runs, on both sides of the band.

    A figure more than the ratchet's one-point tolerance below the baseline
    exits non-zero and writes nothing; one inside the band passes and holds
    the baseline, as it does in the pull-request lanes.
    """
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
            current,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert (result.returncode != 0) is fails
    assert baseline.read_text() == "80.00"


def test_the_committed_baseline_is_read_without_a_checkout() -> None:
    """The fallback uses the contents API, so no workspace path is touched."""
    names = [step.get("name") for step in _steps(PUBLISH_YML)]
    assert "Fetch the committed baselines" not in names
    assert not any(
        "checkout" in str(step.get("uses", "")) for step in _steps(PUBLISH_YML)
    )
    run = _step(PUBLISH_YML, "Ensure baseline files")["run"]
    assert "contents/${encoded}?ref=${GITHUB_SHA}" in run
    assert names.index("Restore baselines") < names.index("Ensure baseline files")


def _ensure(
    tmp_path: Path, *, committed: str, restored: bool, check: bool = True
) -> Path:
    """Run the Ensure step with a stub `curl`; return the working directory."""
    step = _step(PUBLISH_YML, "Ensure baseline files")
    work = tmp_path / "work"
    work.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # The stub serves `committed` for the Rust file only, and fails (as curl
    # --fail does on a 404) for anything else.
    stub = bindir / "curl"
    # Serves `committed` for the Rust file and answers 404 for anything else;
    # a `committed` of "error" answers 500 for every file.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "out=''; url=''\n"
        'while [ $# -gt 0 ]; do case "$1" in --output) out=$2; shift;; '
        "http*) url=$1;; esac; shift; done\n"
        + (
            "printf 500\nexit 0\n"
            if committed == "error"
            else 'case "$url" in */commits/*) exit 22;; esac\nprintf 404\nexit 0\n'
            if committed == "private"
            else (
                'case "$url" in *rust.base*) '
                f"printf '%s\\n' '{committed}' > \"$out\"; printf 200; exit 0;; esac; "
                "printf 404; exit 0\n"
            )
        )
    )
    stub.chmod(0o755)
    if restored:
        (work / "rust.base").write_text("91.0\n")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "GC_RUST_FILE": "rust.base",
        "GC_PYTHON_FILE": "python.base",
        "GH_TOKEN": "t",
        "GITHUB_API_URL": "https://api.example",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SHA": "abc",
        "GITHUB_WORKSPACE": str(work),
    }
    subprocess.run(  # noqa: S603,TID251 - fixed argv, test-only
        ["bash", "-c", step["run"]],  # noqa: S607
        cwd=work,
        env=env,
        check=check,
        capture_output=True,
    )
    return work


def test_a_miss_starts_from_the_committed_file_and_only_then_from_zero(
    tmp_path: Path,
) -> None:
    """The committed figure is the floor on a miss; 0 is the last resort."""
    work = _ensure(tmp_path, committed="77.0", restored=False)

    assert (work / "rust.base").read_text().strip() == "77.0"
    assert (work / "python.base").read_text().strip() == "0"


def test_an_api_failure_is_not_a_missing_file(tmp_path: Path) -> None:
    """A server error must fail the job, not start the baseline at 0."""
    with pytest.raises(subprocess.CalledProcessError):
        _ensure(tmp_path, committed="error", restored=False)


@pytest.mark.parametrize("malformed", ["", "n/a", "nan", "inf", "-3", "140"])
def test_a_malformed_committed_baseline_fails_instead_of_reading_as_zero(
    tmp_path: Path, malformed: str
) -> None:
    """An HTTP 200 whose body is not a percentage must not lower the baseline."""
    with pytest.raises(subprocess.CalledProcessError):
        _ensure(tmp_path, committed=malformed, restored=False)
    assert not (tmp_path / "work" / "rust.base").exists()


def test_a_404_from_an_unreadable_repository_is_not_an_absent_file(
    tmp_path: Path,
) -> None:
    """A 404 counts as absence only when the commit itself can be read."""
    with pytest.raises(subprocess.CalledProcessError):
        _ensure(tmp_path, committed="private", restored=False)


def test_an_absolute_workspace_path_is_made_repository_relative(
    tmp_path: Path,
) -> None:
    """The contents API takes a repository path, not a runner path."""
    run = _step(PUBLISH_YML, "Ensure baseline files")["run"]

    assert '${file#"${GITHUB_WORKSPACE}"/}' in run
    assert "contents/${encoded}" in run


def test_a_restored_baseline_is_never_replaced_by_the_committed_one(
    tmp_path: Path,
) -> None:
    """A prefix match restores a file; the fallback must leave it alone."""
    work = _ensure(tmp_path, committed="77.0", restored=True)

    assert (work / "rust.base").read_text().strip() == "91.0"


# -- the build-only job -----------------------------------------------------


def _selection(run_rust: ModuleType, **overrides: bool | str) -> typ.Any:  # noqa: ANN401
    """Build a ``FeatureSelection`` for a build-only run, with *overrides*."""
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
        """Record each call's arguments and env overrides, then return canned output."""
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

    assert calls[0][0] == [
        "llvm-cov",
        "show-env",
        "--manifest-path",
        str(tmp_path / "Cargo.toml"),
    ]
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


def test_show_env_rejects_unbalanced_quotes_naming_the_key(
    run_rust: ModuleType,
) -> None:
    """A malformed value is a clear error, not a bare shlex traceback."""
    with pytest.raises(ValueError, match="value of BROKEN"):
        run_rust.parse_show_env("BROKEN='unterminated\n")


def test_show_env_rejoins_an_unquoted_value_holding_spaces(
    run_rust: ModuleType,
) -> None:
    """The words after the first are kept, not silently dropped."""
    assert run_rust.parse_show_env("A=one two  three\n") == {"A": "one two three"}


def test_an_empty_profile_name_is_not_rebased(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty LLVM_PROFILE_FILE must not become the bare target directory."""
    seen: dict[str, str] = {}

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        """Return a ``show-env`` listing with an empty profile name.

        For any other call, the environment overrides are recorded in ``seen`` and
        empty output is returned.
        """
        if args[:2] == ["llvm-cov", "show-env"]:
            return "LLVM_PROFILE_FILE=''\nCARGO_LLVM_COV_TARGET_DIR=/w/target\n"
        seen.update(env_overrides or {})
        return ""

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

    assert seen["LLVM_PROFILE_FILE"] == ""


def test_the_publisher_gates_itself_so_the_calling_job_needs_no_condition() -> None:
    """CV-005 refuses an `if` on a publisher job, so the action decides.

    Every step after the gate runs only when it found a trunk push, which is
    the only run that has a hand-over to publish.
    """
    steps = _steps(PUBLISH_YML)
    gate = steps[0]
    assert gate["id"] == "gate"
    for step in steps[1:]:
        assert "steps.gate.outputs.active == 'true'" in _condition(step), (
            f"{step['name']!r} would run without a hand-over"
        )


def test_plain_cargo_build_matches_the_coverage_run_target_selection(
    run_rust: ModuleType,
) -> None:
    """`cargo llvm-cov` runs `cargo test --tests`; a bare build would add examples."""
    args = run_rust.get_build_only_cmd(
        "",
        manifest_path=Path("Cargo.toml"),
        target_dir=Path("t"),
        selection=_selection(run_rust, use_nextest=False, all_targets=False),
    )

    assert "--tests" in args
    assert "--all-targets" not in args


def _stub_show_env(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, printed: str
) -> dict[str, str]:
    """Stub cargo so ``show-env`` prints *printed*; return the build's env."""
    seen: dict[str, str] = {}

    def fake_run_cargo(
        args: list[str],
        *,
        env_overrides: typ.Mapping[str, str] | None = None,
        env_unsets: typ.Iterable[str] = (),
    ) -> str:
        """Return the stubbed ``show-env`` output.

        For any other call, the environment overrides are recorded in ``seen`` and
        empty output is returned.
        """
        if args[:2] == ["llvm-cov", "show-env"]:
            return printed
        seen.update(env_overrides or {})
        return ""

    monkeypatch.setattr(run_rust, "_run_cargo", fake_run_cargo)
    return seen


def _targets(run_rust: ModuleType, tmp_path: Path) -> typ.Any:  # noqa: ANN401
    """Build a ``CoverageTargets`` for a build-only run."""
    return run_rust.CoverageTargets(
        out=tmp_path / "unused",
        lang="rust",
        fmt="cobertura",
        manifest_path=tmp_path / "Cargo.toml",
        github_output=tmp_path / "gh",
        baseline_file=None,
    )


def test_a_plain_cargo_build_names_profiles_as_the_plain_run_does(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """show-env's nextest-style `%32m` would differ from a plain run's `%m`."""
    seen = _stub_show_env(
        run_rust,
        monkeypatch,
        "LLVM_PROFILE_FILE='/w/target/p-%p-%32m.profraw'\n"
        "CARGO_LLVM_COV_TARGET_DIR=/w/target\n",
    )

    run_rust.build_instrumented_tests(
        _targets(run_rust, tmp_path), _selection(run_rust, use_nextest=False)
    )

    assert seen["LLVM_PROFILE_FILE"].endswith("p-%p-%m.profraw")


@pytest.mark.parametrize(
    "name",
    [
        "CARGO_LLVM_COV_TARGET_DIR",
        "CARGO_LLVM_COV_BUILD_DIR",
        "CARGO_BUILD_BUILD_DIR",
    ],
)
def test_an_overridden_cargo_directory_is_refused_not_ignored(
    run_rust: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    """A coverage run honours the override; a build that did not warms nothing."""
    _stub_show_env(run_rust, monkeypatch, "")
    monkeypatch.setenv(name, "/elsewhere")

    with pytest.raises(run_rust.typer.Exit):
        run_rust.build_instrumented_tests(
            _targets(run_rust, tmp_path), _selection(run_rust)
        )


def test_a_separate_build_directory_is_refused_not_ignored(
    run_rust: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured build.build-dir moves cargo-llvm-cov's build; this mode cannot."""
    _stub_show_env(
        run_rust,
        monkeypatch,
        "CARGO_LLVM_COV_TARGET_DIR=/w/target\nCARGO_LLVM_COV_BUILD_DIR=/w/build\n",
    )

    with pytest.raises(run_rust.typer.Exit):
        run_rust.build_instrumented_tests(
            _targets(run_rust, tmp_path), _selection(run_rust)
        )
