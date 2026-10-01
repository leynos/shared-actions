"""Tests for the ``allow-no-tests`` input of the Rust coverage step.

cargo-nextest exits 4 when it finds nothing to run, and ``use-cargo-nextest``
defaults to true, so a crate without tests fails ``generate-coverage``. The
``allow-no-tests`` input opts a caller out of that failure with
``--no-tests=pass``. It defaults off, because a silent pass would also hide a
test filter that accidentally selects nothing. These tests pin the manifest
contract, the rendered command, and the path from the environment variable to
the cargo invocation.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

ACTION_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ACTION_DIR / "scripts"
NO_TESTS_FLAG = "--no-tests=pass"


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
    # Registered before execution: dataclass resolution looks the defining
    # module up in ``sys.modules`` while the class body runs.
    monkeypatch.setitem(sys.modules, "run_rust", module)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed composite action manifest."""
    loaded = yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _command(run_rust: ModuleType, **kwargs: bool) -> list[str]:
    """Render the llvm-cov command with the given flag overrides."""
    return run_rust.get_cargo_coverage_cmd(
        "lcov",
        Path("cov.lcov"),
        "",
        manifest_path=Path("Cargo.toml"),
        with_default=True,
        **kwargs,
    )


def test_input_is_declared_and_defaults_off() -> None:
    """A repository that has tests must not gain a silent pass by upgrading."""
    declared = _manifest()["inputs"]["allow-no-tests"]
    assert declared.get("required", False) is False
    assert declared.get("default") == "false"


def test_input_reaches_the_coverage_script() -> None:
    """The Rust step must forward the input verbatim."""
    steps = _manifest()["runs"]["steps"]
    (rust,) = [step for step in steps if step.get("id") == "rust"]
    assert rust["env"].get("INPUT_ALLOW_NO_TESTS") == "${{ inputs.allow-no-tests }}"


def test_nextest_run_passes_when_no_tests_are_allowed(run_rust: ModuleType) -> None:
    """With nextest and the input set, nextest is told an empty run is fine."""
    args = _command(run_rust, use_nextest=True, allow_no_tests=True)

    assert NO_TESTS_FLAG in args
    assert args.index("nextest") < args.index(NO_TESTS_FLAG) < args.index("--lcov")


@pytest.mark.parametrize(
    ("use_nextest", "allow_no_tests"),
    [(True, False), (False, True), (False, False)],
)
def test_flag_is_absent_unless_nextest_and_the_input_are_both_on(
    run_rust: ModuleType, *, use_nextest: bool, allow_no_tests: bool
) -> None:
    """The flag is a nextest option, and the input is opt-in."""
    args = _command(run_rust, use_nextest=use_nextest, allow_no_tests=allow_no_tests)

    assert NO_TESTS_FLAG not in args


def _run_main_capturing_cargo(
    run_rust: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
) -> list[list[str]]:
    """Run ``main`` with cargo stubbed and return the commands it issued."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "cov.lcov"
    output.write_text("LF:10\nLH:10\n")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
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
        baseline_file=None,
    )
    return calls


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False), (None, False)],
)
def test_environment_input_selects_the_flag_end_to_end(
    run_rust: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    *,
    expected: bool,
) -> None:
    """The action's environment variable decides whether cargo sees the flag."""
    environment = {} if value is None else {"INPUT_ALLOW_NO_TESTS": value}
    monkeypatch.delenv("INPUT_ALLOW_NO_TESTS", raising=False)

    (call,) = _run_main_capturing_cargo(run_rust, tmp_path, monkeypatch, environment)

    assert (NO_TESTS_FLAG in call) is expected
