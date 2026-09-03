"""Tests for generate-coverage utility scripts.

This module exercises helper modules that are executed as separate script
entry points (`run_python`, plus `merge_cobertura`) and validates their
interdependencies. It documents how script-level helpers are composed inside
the GitHub Action runtime for Python coverage flows, plus the cargo-binstall
shell fragment and the action manifest itself. The cargo-nextest installer
script has its own dedicated test module, `test_install_cargo_nextest.py`, and
the `run_rust` script tests live in `test_run_rust.py`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import itertools
import os
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml
from _coverage_test_support import _exit_code, _load_module, run_script
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from plumbum import local

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from types import ModuleType

    from syrupy.assertion import SnapshotAssertion

    from test_support.cmd_mox_stub_adapter import StubManager


def test_merge_cobertura(tmp_path: Path, shell_stubs: StubManager) -> None:
    """``merge_cobertura.py`` merges two files and removes them."""
    rust = tmp_path / "r.xml"
    py = tmp_path / "p.xml"
    rust.write_text("<r/>")
    py.write_text("<p/>")
    out = tmp_path / "merged.xml"

    shell_stubs.register(
        "uvx",
        variants=[
            {
                "match": ["merge-cobertura", str(rust), str(py)],
                "stdout": "<merged/>",
            }
        ],
    )

    env = {
        **shell_stubs.env,
        "RUST_FILE": str(rust),
        "PYTHON_FILE": str(py),
        "OUTPUT_PATH": str(out),
    }
    script = Path(__file__).resolve().parents[1] / "scripts" / "merge_cobertura.py"
    returncode, _, _ = run_script(script, env)
    assert returncode == 0
    assert out.read_text() == "<merged/>"
    assert not rust.exists()
    assert not py.exists()
    calls = shell_stubs.calls_of("uvx")
    assert calls
    assert calls[0].argv[:1] == ["merge-cobertura"]


@pytest.fixture
def run_python_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_python`` module for testing."""
    return _load_module(monkeypatch, "run_python")


def _coverage_python(run_python_module: ModuleType) -> str:
    """Return the expected throwaway venv Python path."""
    if sys.platform == "win32":
        return str(run_python_module.COVERAGE_VENV / "Scripts" / "python.exe")
    return str(run_python_module.COVERAGE_VENV / "bin" / "python")


def _set_fake_coverage_python_cmd(
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> str:
    """Patch the coverage Python command helper to avoid creating a venv."""
    python = _coverage_python(run_python_module)
    monkeypatch.setattr(
        run_python_module,
        "_coverage_python_cmd",
        lambda: local[python],
    )
    return python


def _assert_python_command_structure(parts: list[str]) -> None:
    """Verify common venv Python command structure with slipcover and pytest."""
    assert Path(parts[0]).stem == "python"
    slip_idx = parts.index("-m", 1)
    assert parts[slip_idx : slip_idx + 3] == ["-m", "slipcover", "--branch"]
    pytest_idx = parts.index("pytest")
    assert parts[pytest_idx - 1 : pytest_idx + 2] == ["-m", "pytest", "-v"]


def _assert_coverage_python_path(actual: str, expected: str) -> None:
    """Assert that a formulated command points at the coverage venv Python."""
    actual_path = Path(actual)
    expected_path = Path(expected)
    assert actual_path.parts[-3:-1] == expected_path.parts[-3:-1]
    assert actual_path.stem == expected_path.stem


def _assert_tokens_in_order(parts: list[str], *tokens: str) -> None:
    """Assert that ``tokens`` appear in ``parts`` while preserving order."""
    iterator = iter(parts)
    for token in tokens:
        for part in iterator:
            if part == token:
                break
        else:  # pragma: no cover - AssertionError path
            message = f"{token!r} not found after prior tokens {tokens!r}"
            pytest.fail(message)


def _assert_flag_value_pair(parts: list[str], flag: str, value: str) -> None:
    """Assert that ``flag`` is immediately followed by ``value`` in ``parts``."""
    for candidate in itertools.pairwise(parts):
        if candidate == (flag, value):
            return
    message = f"{flag!r} not paired with {value!r}"
    pytest.fail(message)


def _is_uv_venv_invocation(parts: list[str]) -> bool:
    """Return True when the command parts represent a ``uv venv`` call."""
    return len(parts) > 1 and parts[1] == "venv"


@dataclasses.dataclass
class VenvTestSetup:
    """Scaffolding returned by _setup_coverage_venv_test for venv tests."""

    coverage_venv: Path
    recorded: list[list[str]] = dataclasses.field(default_factory=list)


def _setup_coverage_venv_test(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    python_to_create: str | None = "bin/python",
) -> VenvTestSetup:
    """Patch COVERAGE_VENV and run_cmd; return shared test scaffolding.

    Parameters
    ----------
    python_to_create:
        Relative POSIX path inside the venv to create when ``uv venv``
        is recorded. Pass ``None`` to skip creation (reuse scenario).
    """
    coverage_venv = tmp_path / ".venv-coverage"
    setup = VenvTestSetup(coverage_venv=coverage_venv)

    def fake_run_cmd(cmd: object, *_args: object, **_kwargs: object) -> None:
        parts = list(cmd.formulate())  # type: ignore[attr-defined]
        setup.recorded.append(parts)
        if python_to_create is not None and _is_uv_venv_invocation(parts):
            python_path = coverage_venv / python_to_create
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.touch()

    monkeypatch.setattr(run_python_module, "COVERAGE_VENV", coverage_venv)
    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    return setup


def test_ensure_coverage_venv_returns_coverage_python(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper creates the throwaway coverage venv and returns its Python."""
    setup = _setup_coverage_venv_test(tmp_path, run_python_module, monkeypatch)

    python = run_python_module._ensure_coverage_venv()
    expected_python = (setup.coverage_venv / "bin" / "python").resolve()

    assert python == str(expected_python)
    assert len(setup.recorded) == 3
    venv_parts = setup.recorded[0]
    assert Path(venv_parts[0]).stem == "uv"
    assert venv_parts[1:] == ["venv", str(setup.coverage_venv)]
    sync_parts = setup.recorded[1]
    assert sync_parts[1:] == ["sync", "--inexact", "--python", python]
    install_parts = setup.recorded[2]
    assert install_parts[1:5] == [
        "pip",
        "install",
        "--python",
        str(expected_python),
    ]


def test_ensure_coverage_venv_reuses_existing_coverage_venv(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper does not recreate an existing coverage venv."""
    setup = _setup_coverage_venv_test(
        tmp_path, run_python_module, monkeypatch, python_to_create=None
    )
    python_path = setup.coverage_venv / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.touch()

    assert run_python_module._ensure_coverage_venv() == str(python_path.resolve())
    assert len(setup.recorded) == 2
    assert setup.recorded[0][1:] == [
        "sync",
        "--inexact",
        "--python",
        str(python_path.resolve()),
    ]
    assert setup.recorded[1][1:5] == [
        "pip",
        "install",
        "--python",
        str(python_path.resolve()),
    ]


def test_ensure_coverage_venv_recovers_from_broken_cache(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper recreates a venv whose Python executable is absent."""
    setup = _setup_coverage_venv_test(tmp_path, run_python_module, monkeypatch)
    setup.coverage_venv.mkdir(parents=True)  # broken: dir present, no binary

    python = run_python_module._ensure_coverage_venv()

    venv_calls = [r for r in setup.recorded if len(r) > 1 and r[1] == "venv"]
    assert len(venv_calls) == 1
    assert python == str((setup.coverage_venv / "bin" / "python").resolve())
    assert setup.recorded[-2][1:] == ["sync", "--inexact", "--python", python]
    assert setup.recorded[-1][1:5] == [
        "pip",
        "install",
        "--python",
        python,
    ]


def test_find_coverage_python_returns_none_when_no_executable(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_find_coverage_python() returns None when no binary exists."""
    coverage_venv = tmp_path / ".venv-coverage"
    coverage_venv.mkdir(parents=True)
    monkeypatch.setattr(run_python_module, "COVERAGE_VENV", coverage_venv)

    assert run_python_module._find_coverage_python() is None


def test_find_coverage_python_returns_absolute_path(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_find_coverage_python() resolves relative venv paths before returning."""
    monkeypatch.chdir(tmp_path)
    coverage_venv = Path(".venv-coverage")
    python = coverage_venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(run_python_module, "COVERAGE_VENV", coverage_venv)

    found = run_python_module._find_coverage_python()

    assert found is not None
    assert found == python.resolve()
    assert found.is_absolute()


def test_ensure_coverage_venv_keeps_symlinked_venv_python_path(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not resolve venv Python symlinks back to the system interpreter."""
    setup = _setup_coverage_venv_test(
        tmp_path, run_python_module, monkeypatch, python_to_create=None
    )
    system_python = tmp_path / "usr" / "bin" / "python3.12"
    system_python.parent.mkdir(parents=True)
    system_python.touch()
    venv_python = setup.coverage_venv / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(system_python)
    expected_python = venv_python.absolute()

    assert run_python_module._ensure_coverage_venv() == str(expected_python)

    assert expected_python != system_python.resolve()
    assert len(setup.recorded) == 2
    assert setup.recorded[0][1:] == [
        "sync",
        "--inexact",
        "--python",
        str(expected_python),
    ]
    assert setup.recorded[1][1:5] == [
        "pip",
        "install",
        "--python",
        str(expected_python),
    ]


def test_ensure_coverage_venv_raises_when_created_venv_has_no_python(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_coverage_venv() fails before sync/install if uv creates no Python."""
    setup = _setup_coverage_venv_test(
        tmp_path, run_python_module, monkeypatch, python_to_create=None
    )

    with pytest.raises(RuntimeError, match="Coverage venv Python executable not found"):
        run_python_module._ensure_coverage_venv()

    assert run_python_module._find_coverage_python() is None
    assert len(setup.recorded) == 1
    assert Path(setup.recorded[0][0]).stem == "uv"
    assert setup.recorded[0][1:] == ["venv", str(setup.coverage_venv)]
    assert not [r for r in setup.recorded if len(r) > 1 and r[1] == "sync"]
    assert not [
        r for r in setup.recorded if len(r) > 2 and r[1:3] == ["pip", "install"]
    ]


def _assert_venv_rebuild_commands(
    recorded: list[list[str]],
    coverage_venv: Path,
    python_path: Path,
) -> None:
    """Assert the three-command venv rebuild sequence: venv, sync, pip install."""
    assert len(recorded) == 3
    assert recorded[0][1:] == ["venv", str(coverage_venv)]
    assert recorded[1][1:] == [
        "sync",
        "--inexact",
        "--python",
        str(python_path.resolve()),
    ]
    assert recorded[2][1:5] == [
        "pip",
        "install",
        "--python",
        str(python_path.resolve()),
    ]


def _assert_venv_default_python_rebuild(
    python: str,
    setup: VenvTestSetup,
) -> None:
    """Assert venv was rebuilt and Python resolved to the default POSIX path."""
    expected = setup.coverage_venv / "bin" / "python"
    assert python == str(expected.resolve())
    _assert_venv_rebuild_commands(setup.recorded, setup.coverage_venv, expected)


@pytest.mark.parametrize(
    "broken_state_kind",
    ["file", "symlink"],
    ids=["file-placeholder", "symlink-placeholder"],
)
def test_ensure_coverage_venv_replaces_broken_placeholder(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    broken_state_kind: str,
) -> None:
    """The helper removes file and symlink placeholders before recreating the venv."""
    setup = _setup_coverage_venv_test(tmp_path, run_python_module, monkeypatch)
    if broken_state_kind == "symlink":
        setup.coverage_venv.symlink_to(tmp_path / "not-a-venv")
    else:
        setup.coverage_venv.write_text("not a venv")

    python = run_python_module._ensure_coverage_venv()

    if broken_state_kind == "symlink":
        assert not setup.coverage_venv.is_symlink()
    _assert_venv_default_python_rebuild(python, setup)


def test_ensure_coverage_venv_recreates_invalid_python_candidate(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper rejects non-file Python placeholders before reuse."""
    setup = _setup_coverage_venv_test(
        tmp_path,
        run_python_module,
        monkeypatch,
        python_to_create="Scripts/python.exe",
    )
    (setup.coverage_venv / "bin" / "python").mkdir(parents=True)  # dir, not file

    assert run_python_module._ensure_coverage_venv() == str(
        (setup.coverage_venv / "Scripts" / "python.exe").resolve()
    )
    _assert_venv_rebuild_commands(
        setup.recorded,
        setup.coverage_venv,
        setup.coverage_venv / "Scripts" / "python.exe",
    )


def test_ensure_coverage_venv_targets_venv_python_for_tooling(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tooling installs into the throwaway venv instead of the system Python."""
    setup = _setup_coverage_venv_test(
        tmp_path, run_python_module, monkeypatch, python_to_create=None
    )
    python = setup.coverage_venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()

    assert run_python_module._ensure_coverage_venv() == str(python.resolve())

    assert len(setup.recorded) == 2
    assert setup.recorded[0][1:] == [
        "sync",
        "--inexact",
        "--python",
        str(python.resolve()),
    ]
    parts = setup.recorded[1]
    assert Path(parts[0]).stem == "uv"
    assert parts[1:5] == ["pip", "install", "--python", str(python.resolve())]
    assert "--system" not in parts
    assert set(run_python_module.TOOLING_PACKAGES).issubset(parts)


def test_ensure_coverage_venv_sets_uv_project_environment_for_sync(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uv sync targets the coverage venv via UV_PROJECT_ENVIRONMENT."""
    setup = _setup_coverage_venv_test(
        tmp_path, run_python_module, monkeypatch, python_to_create=None
    )
    python = setup.coverage_venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    sync_environment: list[str | None] = []

    def fake_run_cmd(cmd: object, *_args: object, **_kwargs: object) -> None:
        parts = list(cmd.formulate())  # type: ignore[attr-defined]
        setup.recorded.append(parts)
        if len(parts) > 1 and parts[1] == "sync":
            sync_environment.append(os.environ.get("UV_PROJECT_ENVIRONMENT"))

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)

    assert run_python_module._ensure_coverage_venv() == str(python.resolve())

    assert sync_environment == [str(setup.coverage_venv.resolve())]
    assert "UV_PROJECT_ENVIRONMENT" not in os.environ


def test_coverage_python_cmd_prepares_tools_once(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coverage Python command lazily creates and installs tooling once."""
    coverage_venv = tmp_path / ".venv-coverage"
    python_path = coverage_venv / "bin" / "python"
    recorded: list[list[str]] = []

    def fake_run_cmd(cmd: object, *_args: object, **_kwargs: object) -> None:
        args = list(cmd.formulate())  # type: ignore[attr-defined]
        recorded.append(args)
        if len(args) > 1 and args[1] == "venv":
            python_path.parent.mkdir(parents=True)
            python_path.touch()

    monkeypatch.setattr(run_python_module, "COVERAGE_VENV", coverage_venv)
    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)

    first = run_python_module._coverage_python_cmd()
    second = run_python_module._coverage_python_cmd()

    assert first is second
    parts = list(first.formulate())
    _assert_coverage_python_path(parts[0], str(python_path.resolve()))
    assert len(recorded) == 3
    assert recorded[0][1:] == ["venv", str(coverage_venv)]
    assert recorded[1][1:] == [
        "sync",
        "--inexact",
        "--python",
        str(python_path.resolve()),
    ]
    assert recorded[2][1:5] == [
        "pip",
        "install",
        "--python",
        str(python_path.resolve()),
    ]


def test_coverage_cmd_uses_venv_python(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper wires slipcover/pytest through the throwaway venv."""
    python = _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    cmd = run_python_module.coverage_cmd_for_fmt("coveragepy", Path("coverage.dat"))
    parts = list(cmd.formulate())
    _assert_coverage_python_path(parts[0], python)
    _assert_python_command_structure(parts)


@dataclasses.dataclass(frozen=True)
class CoverageFmtSpec:
    """Pairs a coverage format name with the corresponding file suffix."""

    fmt: str
    suffix: str


def _get_coverage_cmd_parts(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    spec: CoverageFmtSpec,
) -> tuple[list[str], Path]:
    """Build coverage command for format and return parts and output path."""
    out = tmp_path / f"cov.{spec.suffix}"
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    cmd = run_python_module.coverage_cmd_for_fmt(spec.fmt, out)
    parts = list(cmd.formulate())
    _assert_python_command_structure(parts)
    return parts, out


def test_coverage_cmd_cobertura_uses_venv_python(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cobertura format invokes slipcover with ``--xml`` using the venv Python."""
    parts, out = _get_coverage_cmd_parts(
        tmp_path, run_python_module, monkeypatch, CoverageFmtSpec("cobertura", "xml")
    )
    assert parts.count("--xml") == 1
    _assert_tokens_in_order(parts, "--xml", "--out")
    _assert_flag_value_pair(parts, "--out", str(out))


def test_non_cobertura_formats_do_not_emit_cobertura_flags(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Cobertura formats do not emit Cobertura output flags."""
    parts, out = _get_coverage_cmd_parts(
        tmp_path, run_python_module, monkeypatch, CoverageFmtSpec("coveragepy", "dat")
    )
    assert "--xml" not in parts
    assert str(out) not in parts
    assert "--out" not in parts


def test_pytest_xdist_is_installed_with_tooling(
    run_python_module: ModuleType,
) -> None:
    """``pytest-xdist`` ships alongside slipcover so ``-n`` is available."""
    assert "pytest-xdist" in run_python_module.TOOLING_PACKAGES


def test_coverage_args_omits_workers_when_empty(
    tmp_path: Path,
    run_python_module: ModuleType,
) -> None:
    """An empty workers value preserves the historical serial pytest call."""
    args = run_python_module._coverage_args("cobertura", tmp_path / "cov.xml", "")
    assert "-n" not in args


@pytest.mark.parametrize("workers", ["auto", "logical", "4", "1"])
def test_coverage_args_appends_workers_flag(
    tmp_path: Path,
    run_python_module: ModuleType,
    workers: str,
) -> None:
    """Non-empty workers values append ``-n <workers>`` after pytest args."""
    args = run_python_module._coverage_args("cobertura", tmp_path / "cov.xml", workers)
    assert args[-2:] == ["-n", workers]
    pytest_idx = args.index("pytest")
    n_idx = args.index("-n")
    assert pytest_idx < n_idx


def test_coverage_cmd_for_fmt_threads_workers_through(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coverage_cmd_for_fmt forwards ``workers`` into the slipcover argv."""
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    cmd = run_python_module.coverage_cmd_for_fmt("cobertura", tmp_path / "cov.xml", "2")
    parts = list(cmd.formulate())
    assert parts[-2:] == ["-n", "2"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("auto", "auto"),
        ("AUTO", "auto"),
        (" logical ", "logical"),
        ("4", "4"),
        ("1", "1"),
    ],
)
def test_normalize_pytest_workers_accepts_valid_values(
    run_python_module: ModuleType,
    raw: str | None,
    expected: str,
) -> None:
    """Valid worker values normalize to the lowercase/stripped form."""
    assert run_python_module._normalize_pytest_workers(raw) == expected


@pytest.mark.parametrize("raw", ["banana", "-1", "4.0", "auto2", "two", "0"])
def test_normalize_pytest_workers_rejects_invalid_values(
    run_python_module: ModuleType,
    raw: str,
) -> None:
    """Junk worker values exit with the configuration-error code."""
    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module._normalize_pytest_workers(raw)
    assert _exit_code(excinfo.value) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("auto", "auto"),
        ("AUTO", "auto"),
        (" logical ", "logical"),
        ("4", "4"),
        ("1", "1"),
    ],
)
def test_parse_pytest_workers_returns_normalized_value(
    run_python_module: ModuleType,
    raw: str | None,
    expected: str,
) -> None:
    """The pure parser returns the same normalized value as the Typer wrapper."""
    assert run_python_module._parse_pytest_workers(raw) == expected


@pytest.mark.parametrize("raw", ["banana", "-1", "4.0", "auto2", "two", "0"])
def test_parse_pytest_workers_raises_value_error_on_invalid(
    run_python_module: ModuleType,
    raw: str,
) -> None:
    """Invalid inputs raise ValueError without any Typer side-effects."""
    with pytest.raises(ValueError, match="Invalid pytest-workers value") as excinfo:
        run_python_module._parse_pytest_workers(raw)
    message = str(excinfo.value)
    assert repr(raw) in message
    assert "positive integer" in message
    assert '"auto"' in message
    assert '"logical"' in message


_PYTEST_PARSER_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_WHITESPACE_ST = st.text(alphabet=" \t", max_size=4)


@_PYTEST_PARSER_SETTINGS
@given(
    name=st.sampled_from(["auto", "logical"]),
    upper_mask=st.integers(min_value=0, max_value=(1 << 7) - 1),
    pad_pair=st.tuples(_WHITESPACE_ST, _WHITESPACE_ST),
)
def test_parse_pytest_workers_normalizes_named_values(
    run_python_module: ModuleType,
    name: str,
    upper_mask: int,
    pad_pair: tuple[str, str],
) -> None:
    """Named values normalize to lowercase regardless of casing or padding."""
    leading, trailing = pad_pair
    mixed = "".join(
        ch.upper() if (upper_mask >> i) & 1 else ch for i, ch in enumerate(name)
    )
    raw = f"{leading}{mixed}{trailing}"
    assert run_python_module._parse_pytest_workers(raw) == name


@_PYTEST_PARSER_SETTINGS
@given(
    value=st.integers(min_value=1, max_value=10**9),
    leading=_WHITESPACE_ST,
    trailing=_WHITESPACE_ST,
)
def test_parse_pytest_workers_round_trips_positive_integers(
    run_python_module: ModuleType,
    value: int,
    leading: str,
    trailing: str,
) -> None:
    """Positive integer strings round-trip through the parser unchanged."""
    digits = str(value)
    raw = f"{leading}{digits}{trailing}"
    assert run_python_module._parse_pytest_workers(raw) == digits


@_PYTEST_PARSER_SETTINGS
@given(blank=_WHITESPACE_ST)
def test_parse_pytest_workers_treats_whitespace_only_as_empty(
    run_python_module: ModuleType,
    blank: str,
) -> None:
    """Whitespace-only strings (and the empty string) disable parallelism."""
    assert run_python_module._parse_pytest_workers(blank) == ""


@_PYTEST_PARSER_SETTINGS
@given(value=st.integers(max_value=0))
def test_parse_pytest_workers_rejects_non_positive_integers(
    run_python_module: ModuleType,
    value: int,
) -> None:
    """Zero and negative integers raise ValueError with the canonical message."""
    raw = str(value)
    with pytest.raises(ValueError, match="Invalid pytest-workers value"):
        run_python_module._parse_pytest_workers(raw)


def test_resolve_pytest_workers_defaults_to_auto_when_env_unset(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No CLI override and no env var falls back to the documented default."""
    monkeypatch.delenv("INPUT_PYTEST_WORKERS", raising=False)
    assert (
        run_python_module._resolve_pytest_workers(None)
        == run_python_module.DEFAULT_PYTEST_WORKERS
    )


def test_resolve_pytest_workers_reads_env_var(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var supplies the value when the CLI option is omitted."""
    monkeypatch.setenv("INPUT_PYTEST_WORKERS", "3")
    assert run_python_module._resolve_pytest_workers(None) == "3"


def test_resolve_pytest_workers_empty_env_disables_xdist(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty env value disables parallelism (does not fall back to auto)."""
    monkeypatch.setenv("INPUT_PYTEST_WORKERS", "")
    assert run_python_module._resolve_pytest_workers(None) == ""


def test_resolve_pytest_workers_cli_overrides_env(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI option takes precedence over the env var when supplied."""
    monkeypatch.setenv("INPUT_PYTEST_WORKERS", "auto")
    assert run_python_module._resolve_pytest_workers("") == ""
    assert run_python_module._resolve_pytest_workers("8") == "8"


def test_resolve_pytest_workers_raises_value_error_on_invalid_env(
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid inputs propagate ValueError without Typer side-effects."""
    monkeypatch.setenv("INPUT_PYTEST_WORKERS", "banana")
    with pytest.raises(ValueError, match="Invalid pytest-workers value"):
        run_python_module._resolve_pytest_workers(None)


def test_main_translates_invalid_workers_into_typer_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_python_module: ModuleType,
) -> None:
    """``main`` is the sole CLI boundary that converts ValueError into Exit(2)."""
    output = tmp_path / "cov.xml"
    output.write_text(
        "<coverage lines-covered='1' lines-valid='1' />",
        encoding="utf-8",
    )
    github_output = tmp_path / "gh.txt"

    def fake_run_cmd(*_args: object, **_kwargs: object) -> None:
        pytest.fail("run_cmd must not be invoked when worker validation fails")

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    monkeypatch.delenv("INPUT_PYTEST_WORKERS", raising=False)

    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.main(
            output, "python", "cobertura", github_output, None, "banana"
        )
    assert _exit_code(excinfo.value) == 2
    assert "Invalid pytest-workers value" in capsys.readouterr().err


def test_tmp_coveragepy_xml_invokes_venv_python(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coverage.py exporter also reuses the venv Python."""
    out = tmp_path / "coveragepy.dat"
    xml_path = out.with_suffix(".xml")
    recorded: dict[str, list[str]] = {}

    def fake_run_cmd(cmd: object, *_args: object, **_kwargs: object) -> None:
        recorded["cmd"] = list(cmd.formulate())  # type: ignore[attr-defined]
        xml_path.write_text("<coverage/>", encoding="utf-8")

    run_python_module.run_cmd = fake_run_cmd  # type: ignore[assignment]

    python = _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    with run_python_module.tmp_coveragepy_xml(out) as generated:
        assert generated == xml_path
        assert xml_path.exists()

    assert not xml_path.exists()
    parts = recorded["cmd"]
    _assert_coverage_python_path(parts[0], python)
    assert parts[-5:] == ["-m", "coverage", "xml", "-o", str(xml_path)]


def test_run_python_cobertura_passes_out_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> None:
    """``main`` keeps Cobertura output wiring explicit when invoking slipcover."""
    output = tmp_path / "cov.xml"
    output.write_text(
        "<coverage lines-covered='1' lines-valid='1' />",
        encoding="utf-8",
    )
    github_output = tmp_path / "gh.txt"
    recorded: list[tuple[list[str], str | None]] = []

    def fake_run_cmd(cmd: object, *_args: object, **kwargs: object) -> None:
        recorded.append(
            (list(cmd.formulate()), typ.cast("str | None", kwargs.get("method")))
        )  # type: ignore[attr-defined]

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)

    run_python_module.main(output, "python", "cobertura", github_output, None)

    assert len(recorded) == 1
    parts = recorded[0][0]
    assert recorded[0][1] == "run_fg"
    _assert_python_command_structure(parts)
    _assert_tokens_in_order(parts, "--xml", "--out")
    _assert_flag_value_pair(parts, "--out", str(output))
    data = github_output.read_text().splitlines()
    assert f"file={output}" in data
    assert "percent=100.00" in data


def _run_main_with_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
    workers: str,
) -> list[str]:
    """Invoke ``main`` under a fake coverage command and return the recorded argv.

    Sets up the Cobertura XML stub, patches ``run_cmd`` to record the invocation,
    patches the coverage-venv Python command, and clears ``INPUT_PYTEST_WORKERS``
    so the supplied *workers* value is the sole source of truth. Stdout capture
    is left to the caller via ``capsys``.
    """
    output = tmp_path / "cov.xml"
    output.write_text(
        "<coverage lines-covered='1' lines-valid='1' />",
        encoding="utf-8",
    )
    github_output = tmp_path / "gh.txt"
    recorded: list[list[str]] = []

    def fake_run_cmd(cmd: object, *_args: object, **_kwargs: object) -> None:
        recorded.append(list(cmd.formulate()))  # type: ignore[attr-defined]

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    monkeypatch.delenv("INPUT_PYTEST_WORKERS", raising=False)

    run_python_module.main(output, "python", "cobertura", github_output, None, workers)

    assert len(recorded) == 1, (
        f"expected exactly one coverage invocation, got {len(recorded)}"
    )
    return recorded[0]


def test_main_threads_pytest_workers_into_slipcover_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_python_module: ModuleType,
) -> None:
    """``main`` forwards the resolved workers value to slipcover's pytest argv."""
    parts = _run_main_with_workers(tmp_path, monkeypatch, run_python_module, "3")
    stdout = capsys.readouterr().out
    assert parts[-2:] == ["-n", "3"], (
        f"workers value must reach slipcover's pytest argv, got {parts!r}"
    )
    assert "Pytest workers: 3 (parallel via pytest-xdist)" in stdout


def test_main_logs_serial_run_when_workers_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_python_module: ModuleType,
) -> None:
    """An empty workers value logs the serial-run notice and omits ``-n``."""
    parts = _run_main_with_workers(tmp_path, monkeypatch, run_python_module, "")
    stdout = capsys.readouterr().out
    assert "-n" not in parts
    assert "Pytest workers: disabled (serial pytest run)" in stdout


def test_cobertura_detail(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` handles per-line detail."""
    xml = tmp_path / "cov.xml"
    xml.write_text(
        """
<coverage>
  <packages>
    <package>
      <classes>
        <class>
          <lines>
            <line hits='1'/>
            <line hits='0'/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
        """
    )
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "50.00"


def test_cobertura_root_totals(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` falls back to root totals."""
    xml = tmp_path / "root.xml"
    xml.write_text("<coverage lines-covered='81' lines-valid='100' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "81.00"


def test_cobertura_zero_lines(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` handles zero totals."""
    xml = tmp_path / "zero.xml"
    xml.write_text("<coverage lines-covered='0' lines-valid='0' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "0.00"


def test_cobertura_malformed_xml(tmp_path: Path, run_python_module: ModuleType) -> None:
    """Malformed XML raises ``typer.Exit``."""
    xml = tmp_path / "bad.xml"
    xml.write_text("<coverage>")
    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert _exit_code(excinfo.value) == 1


def test_run_python_coveragepy_empty_xml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> None:
    """Coverage.py format handles empty XML output and moves the data file."""
    output = tmp_path / "coveragepy.dat"
    github_output = tmp_path / "gh.txt"
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_text("payload", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run_cmd(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    python = _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    captured_python: list[str] = []

    @contextlib.contextmanager
    def fake_tmp_coveragepy_xml(_out: Path) -> typ.Iterator[Path]:
        captured_python.append(
            next(iter(run_python_module._coverage_python_cmd().formulate()))
        )
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text(
            "<coverage lines-covered='0' lines-valid='0' />",
            encoding="utf-8",
        )
        try:
            yield xml_path
        finally:
            xml_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        run_python_module, "tmp_coveragepy_xml", fake_tmp_coveragepy_xml
    )

    run_python_module.main(output, "python", "coveragepy", github_output, None)

    assert len(captured_python) == 1
    _assert_coverage_python_path(captured_python[0], python)

    captured = capsys.readouterr()
    assert "Current coverage: 0.00%" in captured.out

    assert output.read_text(encoding="utf-8") == "payload"
    assert not coverage_file.exists()

    data = github_output.read_text(encoding="utf-8").splitlines()
    assert f"file={output}" in data
    assert "percent=0.00" in data


def test_main_echoes_previous_coverage_when_baseline_present(
    tmp_path: Path,
    run_python_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() logs 'Previous coverage: ...%' when a baseline is provided."""
    monkeypatch.chdir(tmp_path)
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_text("payload", encoding="utf-8")

    def fake_run_cmd(_cmd: object, **_kw: object) -> None:
        pass

    @contextlib.contextmanager
    def fake_tmp_coveragepy_xml(out: Path) -> typ.Iterator[Path]:
        xml = out.with_suffix(".xml")
        xml.write_text(
            "<coverage lines-covered='1' lines-valid='1'/>", encoding="utf-8"
        )
        try:
            yield xml
        finally:
            xml.unlink(missing_ok=True)

    def fake_read_previous_coverage(_baseline: Path | None) -> float | None:
        return 42.0

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        run_python_module, "tmp_coveragepy_xml", fake_tmp_coveragepy_xml
    )
    monkeypatch.setattr(
        run_python_module, "read_previous_coverage", fake_read_previous_coverage
    )
    _set_fake_coverage_python_cmd(monkeypatch, run_python_module)

    out = tmp_path / "cov.dat"
    gh = tmp_path / "gh.txt"
    run_python_module.main(
        output_path=out,
        lang="python",
        fmt="coveragepy",
        github_output=gh,
        baseline_file=tmp_path / "baseline.txt",
    )

    captured = capsys.readouterr()
    assert "Previous coverage: 42.0%" in captured.out


def test_run_python_coveragepy_malformed_xml_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> None:
    """Malformed coverage.py XML propagates Typer exits."""
    output = tmp_path / "coveragepy.dat"
    github_output = tmp_path / "gh.txt"
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_text("payload", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run_cmd(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)
    python = _set_fake_coverage_python_cmd(monkeypatch, run_python_module)
    captured_python: list[str] = []

    @contextlib.contextmanager
    def fake_tmp_coveragepy_xml(_out: Path) -> typ.Iterator[Path]:
        captured_python.append(
            next(iter(run_python_module._coverage_python_cmd().formulate()))
        )
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text("<coverage>", encoding="utf-8")
        try:
            yield xml_path
        finally:
            xml_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        run_python_module, "tmp_coveragepy_xml", fake_tmp_coveragepy_xml
    )

    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.main(output, "python", "coveragepy", github_output, None)

    assert len(captured_python) == 1
    _assert_coverage_python_path(captured_python[0], python)

    assert _exit_code(excinfo.value) == 1
    assert coverage_file.exists()
    assert not github_output.exists()


def test_cobertura_missing_file(tmp_path: Path, run_python_module: ModuleType) -> None:
    """Missing Cobertura files raise ``typer.Exit``."""
    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(
            tmp_path / "absent.xml"
        )
    assert _exit_code(excinfo.value) == 1


def test_cobertura_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_python_module: ModuleType,
) -> None:
    """Permission errors when reading Cobertura files raise ``typer.Exit``."""
    xml = tmp_path / "nope.xml"
    xml.write_text("<coverage/>")

    def raise_permission_error(*_: object, **__: object) -> object:
        message = "denied"
        raise PermissionError(message)

    import coverage_parsers

    monkeypatch.setattr(coverage_parsers.etree, "parse", raise_permission_error)

    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert _exit_code(excinfo.value) == 1


# ---------------------------------------------------------------------------
# Integration tests - run_python.py via run_script()
# ---------------------------------------------------------------------------


def _generate_coverage_action() -> dict[str, object]:
    """Return the generate-coverage action contract."""
    action = Path(__file__).resolve().parents[1] / "action.yml"
    with action.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    assert isinstance(loaded, dict)
    return loaded


def _generate_coverage_steps() -> list[dict[str, object]]:
    """Return the generate-coverage action steps."""
    runs = _generate_coverage_action().get("runs")
    assert isinstance(runs, dict)
    steps = runs.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return typ.cast("list[dict[str, object]]", steps)


def _generate_coverage_step(step_name: str) -> dict[str, object]:
    """Return a named generate-coverage action step."""
    step = next(
        (
            step
            for step in _generate_coverage_steps()
            if isinstance(step, dict) and step.get("name") == step_name
        ),
        None,
    )
    assert step is not None, f"Missing generate-coverage step: {step_name}"
    return step


def test_generate_coverage_ensures_binstall_before_llvm_cov() -> None:
    """cargo-binstall must exist before cargo-llvm-cov invokes cargo binstall."""
    steps = _generate_coverage_steps()
    step_names = [step.get("name") for step in steps]

    assert step_names.index("Ensure cargo-binstall") < step_names.index(
        "Install cargo-llvm-cov"
    )


def test_generate_coverage_binstall_is_not_nextest_only() -> None:
    """cargo-llvm-cov also needs cargo-binstall when nextest is disabled."""
    step = _generate_coverage_step("Ensure cargo-binstall")
    condition = step.get("if")

    assert isinstance(condition, str)
    assert "steps.detect.outputs.lang == 'rust'" in condition
    assert "steps.detect.outputs.lang == 'mixed'" in condition
    assert "use-cargo-nextest" not in condition


def _ensure_binstall_script() -> str:
    """Return the shell body for the cargo-binstall installation step."""
    run_script = _generate_coverage_step("Ensure cargo-binstall").get("run")

    assert isinstance(run_script, str)
    return run_script


def _write_executable(path: Path, content: str) -> None:
    """Write an executable test double."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@dataclasses.dataclass(frozen=True)
class _BinstallScriptResult:
    """Capture the outcome of running the Ensure cargo-binstall shell body."""

    returncode: int
    stdout: str
    stderr: str


def _run_ensure_binstall_script(tmp_path: Path) -> _BinstallScriptResult:
    """Execute the Ensure cargo-binstall shell body in an isolated PATH."""
    env = {
        **os.environ,
        "CARGO_HOME": str(tmp_path / "cargo-home"),
        "GITHUB_PATH": str(tmp_path / "github-path"),
        "HOME": str(tmp_path / "home"),
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }
    command = local["/bin/bash"]["-c", _ensure_binstall_script()]
    result = run_plumbum_command(command, method="run", env=env)
    return _BinstallScriptResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _write_fake_binstall_installer(
    tmp_path: Path,
    *,
    installed_version: str = "1.19.1",
) -> None:
    """Write fake curl, sha256sum, and bash commands for installer-path tests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    install_log = tmp_path / "installer.log"
    version_log = tmp_path / "binstall-version.log"
    checksum = "d3a93702160e0ec03e2a4e996855db1f01adee801fb84a43add24e0877ef8eae"

    _write_executable(
        bin_dir / "curl",
        """#!/bin/sh
output=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ]; then
        shift
        output="$1"
    fi
    shift
done
if [ -z "$output" ]; then
    exit 2
fi
printf '%s\\n' "fake installer" > "$output"
""",
    )
    _write_executable(
        bin_dir / "sha256sum",
        f"""#!/bin/sh
printf '%s  %s\\n' "{checksum}" "$1"
""",
    )
    _write_executable(
        bin_dir / "bash",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{install_log}"
printf '%s\\n' "${{BINSTALL_VERSION:-UNSET}}" >> "{version_log}"
mkdir -p "$CARGO_HOME/bin"
cat > "$CARGO_HOME/bin/cargo-binstall" <<'ENDOFINSTALL'
#!/bin/sh
printf '%s\\n' "cargo-binstall {installed_version}"
ENDOFINSTALL
chmod +x "$CARGO_HOME/bin/cargo-binstall"
""",
    )


def _write_existing_cargo_binstall(tmp_path: Path, version: str) -> None:
    """Write an existing cargo-binstall test double into PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls_log = tmp_path / "existing-binstall.log"
    _write_executable(
        bin_dir / "cargo-binstall",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls_log}"
printf '%s\\n' "cargo-binstall {version}"
""",
    )


@dataclasses.dataclass(frozen=True)
class _BinstallVersionCase:
    """Describe one existing-cargo-binstall version-comparison outcome."""

    existing_version: str
    arrange_pinned_installer: bool
    ran_pinned_installer: bool
    expected_message: str
    expected_stream: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _BinstallVersionCase(
                "1.19.1",
                arrange_pinned_installer=False,
                ran_pinned_installer=False,
                expected_message=(
                    "cargo-binstall already installed: cargo-binstall 1.19.1"
                ),
                expected_stream="stdout",
            ),
            id="fast-path-verified-version",
        ),
        pytest.param(
            _BinstallVersionCase(
                "1.15.0",
                arrange_pinned_installer=True,
                ran_pinned_installer=True,
                expected_message=(
                    "version mismatch: expected 1.19.1, found cargo-binstall 1.15.0"
                ),
                expected_stream="stderr",
            ),
            id="mismatch-installs-pinned-version",
        ),
        pytest.param(
            _BinstallVersionCase(
                "1.19.10",
                arrange_pinned_installer=True,
                ran_pinned_installer=True,
                expected_message=(
                    "version mismatch: expected 1.19.1, found cargo-binstall 1.19.10"
                ),
                expected_stream="stderr",
            ),
            id="longer-version-look-alike-is-rejected",
        ),
    ],
)
def test_generate_coverage_binstall_version_comparison_outcomes(
    case: _BinstallVersionCase,
    tmp_path: Path,
) -> None:
    """Existing cargo-binstall versions are compared exactly, not by substring.

    A verified existing binary is reused without installing; anything else,
    including a longer version string that merely starts with the pin (a
    ``1.19.10`` look-alike for ``1.19.1``), falls through to the pinned
    installer.
    """
    _write_existing_cargo_binstall(tmp_path, case.existing_version)
    if case.arrange_pinned_installer:
        _write_fake_binstall_installer(tmp_path)
    else:
        _write_executable(
            tmp_path / "bin" / "curl",
            """#!/bin/sh
echo "curl should not run for a verified cargo-binstall" >&2
exit 99
""",
        )

    result = _run_ensure_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    haystack = result.stdout if case.expected_stream == "stdout" else result.stderr
    assert case.expected_message in haystack
    if case.ran_pinned_installer:
        assert (tmp_path / "installer.log").read_text(encoding="utf-8")
        assert "cargo-binstall cargo-binstall 1.19.1 verified" in result.stdout
    else:
        assert (tmp_path / "existing-binstall.log").read_text(
            encoding="utf-8"
        ) == "-V\n"


def test_generate_coverage_binstall_install_verifies_installed_version(
    tmp_path: Path,
) -> None:
    """The install path fails when the installed binary has the wrong version."""
    _write_fake_binstall_installer(tmp_path, installed_version="1.15.0")

    result = _run_ensure_binstall_script(tmp_path)

    assert result.returncode == 1
    assert "cargo-binstall version verification failed: expected 1.19.1" in (
        result.stderr
    )


def test_generate_coverage_binstall_exports_pinned_version_to_installer(
    tmp_path: Path,
) -> None:
    """The pinned version is exported so the child installer inherits it."""
    _write_fake_binstall_installer(tmp_path)

    result = _run_ensure_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    version_seen = (tmp_path / "binstall-version.log").read_text(encoding="utf-8")
    # Without `export`, the installer subshell sees BINSTALL_VERSION unset and
    # would silently fall back to releases/latest.
    assert version_seen.strip() == "v1.19.1"


def test_generate_coverage_binstall_appends_cargo_bin_to_github_path(
    tmp_path: Path,
) -> None:
    """A successful install appends the Cargo bin directory to GITHUB_PATH."""
    _write_fake_binstall_installer(tmp_path)

    result = _run_ensure_binstall_script(tmp_path)

    assert result.returncode == 0, result.stderr
    github_path = (tmp_path / "github-path").read_text(encoding="utf-8")
    expected_bin = str(tmp_path / "cargo-home" / "bin")
    assert expected_bin in github_path.splitlines()


def test_generate_coverage_binstall_checksum_mismatch_aborts(
    tmp_path: Path,
) -> None:
    """A bad installer checksum aborts before the installer script runs."""
    _write_fake_binstall_installer(tmp_path)
    # Override sha256sum to report a non-matching digest.
    _write_executable(
        tmp_path / "bin" / "sha256sum",
        """#!/bin/sh
z16=0000000000000000
printf '%s  %s\\n' "$z16$z16$z16$z16" "$1"
""",
    )

    result = _run_ensure_binstall_script(tmp_path)

    assert result.returncode == 1
    assert "install script checksum mismatch" in result.stderr
    # The installer script must not run when the checksum does not match.
    assert not (tmp_path / "installer.log").exists()


def _python_step_env_contract() -> dict[str, str]:
    """Return the env contract for the Python coverage step."""
    steps = _generate_coverage_steps()
    python_step = next(
        step for step in steps if isinstance(step, dict) and step.get("id") == "python"
    )
    env = python_step.get("env")
    assert isinstance(env, dict)
    assert all(isinstance(key, str) for key in env)
    assert all(isinstance(value, str) for value in env.values())
    return typ.cast("dict[str, str]", env)


def _write_fake_uv(
    tmp_path: Path,
    *,
    venv_exit: int = 0,
    sync_exit: int = 0,
) -> tuple[Path, Path]:
    """Write a fake uv executable and return its bin directory and log path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv-calls.log"
    uv = bin_dir / "uv"
    uv.write_text(
        f"""#!/usr/bin/env sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "venv" ]; then
    if [ {venv_exit} -ne 0 ]; then
        echo "uv venv exploded" >&2
        exit {venv_exit}
    fi
    mkdir -p "$2/bin"
    cat > "$2/bin/python" <<'PY'
#!/usr/bin/env sh
exit 0
PY
    chmod +x "$2/bin/python"
    exit 0
fi
if [ "$1" = "sync" ]; then
    if [ {sync_exit} -ne 0 ]; then
        echo "uv sync exploded" >&2
        exit {sync_exit}
    fi
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return bin_dir, log


def _python_integration_env(
    tmp_path: Path,
    shell_stubs: StubManager,
    bin_dir: Path,
) -> dict[str, str]:
    """Return environment for run_python.py integration tests."""
    python_env = _python_step_env_contract()
    assert python_env["INPUT_OUTPUT_PATH"] == "${{ inputs.output-path }}"
    assert python_env["DETECTED_LANG"] == "${{ steps.detect.outputs.lang }}"
    assert python_env["DETECTED_FMT"] == "${{ steps.detect.outputs.fmt }}"
    assert python_env["BASELINE_PYTHON_FILE"] == "${{ inputs.baseline-python-file }}"
    assert python_env["INPUT_PYTEST_WORKERS"] == "${{ inputs.pytest-workers }}"
    out = tmp_path / "cov.xml"
    gh = tmp_path / "gh.txt"
    out.write_text("<coverage lines-covered='1' lines-valid='1'/>", encoding="utf-8")
    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "python",
        "DETECTED_FMT": "cobertura",
        "BASELINE_PYTHON_FILE": str(tmp_path / "baseline-python.txt"),
        "GITHUB_OUTPUT": str(gh),
        # Exercise the INPUT_PYTEST_WORKERS path explicitly; a fixed value
        # also makes the test independent of the action.yml default.
        "INPUT_PYTEST_WORKERS": "2",
    }
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def _run_integration_script(
    tmp_path: Path,
    shell_stubs: StubManager,
    bin_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str, str]:
    """Set up env, chdir, and invoke run_python.py; return (rc, stdout, stderr)."""
    env = _python_integration_env(tmp_path, shell_stubs, bin_dir)
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_python.py"
    return run_script(script, env)


@pytest.mark.skipif(sys.platform == "win32", reason="fake uv helper emits POSIX sh")
def test_run_python_integration_cobertura_success(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """run_python.py creates the venv, syncs deps, installs tooling.

    The script also writes outputs.
    """
    bin_dir, log = _write_fake_uv(tmp_path)

    returncode, _stdout, _stderr = _run_integration_script(
        tmp_path, shell_stubs, bin_dir, monkeypatch
    )

    uv_calls = log.read_text(encoding="utf-8").splitlines()
    venv_calls = [c for c in uv_calls if c.startswith("venv ")]
    sync_calls = [c for c in uv_calls if c.startswith("sync ")]
    pip_calls = [c for c in uv_calls if c.startswith("pip install ")]
    assert returncode == 0
    assert venv_calls, "uv venv must be called to create the coverage venv"
    assert sync_calls, "uv sync must be called to install project deps"
    assert pip_calls, "uv pip install must be called to install tooling"
    pip_args = pip_calls[0].split()
    assert "--python" in pip_args
    assert "--system" not in pip_args
    # Slipcover must carry a version floor so that an older slipcover already
    # installed by `uv sync` gets upgraded for the xdist plugin support.
    assert any(arg.startswith("slipcover>=") for arg in pip_args), (
        f"slipcover must be pinned with a version floor in {pip_args!r}"
    )
    assert "pytest" in pip_args
    assert "pytest-xdist" in pip_args
    assert "coverage" in pip_args
    gh = tmp_path / "gh.txt"
    assert gh.exists(), "GITHUB_OUTPUT file must be written"
    gh_content = gh.read_text(encoding="utf-8")
    assert gh_content.replace(tmp_path.as_posix(), "<TMP>") == snapshot(
        name="run_python_cobertura_github_output"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fake uv helper emits POSIX sh")
def test_run_python_integration_uses_env_fallbacks_for_omitted_cli_args(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted CLI args are resolved from GitHub Actions environment variables."""
    bin_dir, _log = _write_fake_uv(tmp_path)
    returncode, _stdout, _stderr = _run_integration_script(
        tmp_path, shell_stubs, bin_dir, monkeypatch
    )

    gh_content = (tmp_path / "gh.txt").read_text(encoding="utf-8").splitlines()
    assert returncode == 0
    assert f"file={tmp_path / 'cov.xml'}" in gh_content
    assert "percent=100.00" in gh_content


@pytest.mark.skipif(sys.platform == "win32", reason="fake uv helper emits POSIX sh")
def test_run_python_integration_mixed_lang_path(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_python.py renames the output file for mixed-lang projects.

    The output path receives a .python infix.
    """
    bin_dir, _log = _write_fake_uv(tmp_path)
    out = tmp_path / "cov.xml"
    gh = tmp_path / "gh.txt"
    out.write_text("<coverage lines-covered='1' lines-valid='1'/>", encoding="utf-8")
    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "mixed",
        "DETECTED_FMT": "cobertura",
        "BASELINE_PYTHON_FILE": "",
        "GITHUB_OUTPUT": str(gh),
    }
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    monkeypatch.chdir(tmp_path)
    # Provide the expected renamed file so coverage parser can read it
    mixed_out = tmp_path / "cov.python.xml"
    mixed_out.write_text(
        "<coverage lines-covered='1' lines-valid='1'/>", encoding="utf-8"
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_python.py"
    returncode, _stdout, _stderr = run_script(script, env)

    assert returncode == 0
    gh_content = gh.read_text(encoding="utf-8")
    assert "cov.python.xml" in gh_content, (
        "mixed-lang output path must include .python infix"
    )


@dataclasses.dataclass(frozen=True)
class UvFailureSpec:
    """Pairs a fake-uv exit-code configuration with the expected stderr fragment."""

    write_kwargs: dict[str, int]
    expected_in_stderr: str | None


@pytest.mark.skipif(sys.platform == "win32", reason="fake uv helper emits POSIX sh")
@pytest.mark.parametrize(
    "spec",
    [
        UvFailureSpec(write_kwargs={"venv_exit": 1}, expected_in_stderr=None),
        UvFailureSpec(
            write_kwargs={"sync_exit": 2}, expected_in_stderr="uv sync failed"
        ),
    ],
    ids=["uv-venv-fails", "uv-sync-fails"],
)
def test_run_python_integration_uv_failure_modes(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
    spec: UvFailureSpec,
) -> None:
    """run_python.py exits non-zero when uv venv or uv sync fails."""
    bin_dir, _log = _write_fake_uv(tmp_path, **spec.write_kwargs)

    returncode, _stdout, stderr = _run_integration_script(
        tmp_path, shell_stubs, bin_dir, monkeypatch
    )

    assert returncode != 0
    if spec.expected_in_stderr is not None:
        assert spec.expected_in_stderr in stderr


@pytest.mark.skipif(sys.platform == "win32", reason="fake uv helper emits POSIX sh")
def test_run_python_integration_venv_python_symlink_targets_venv(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uv receives the venv symlink path, not the resolved system Python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv-calls.log"
    system_python = tmp_path / "usr" / "bin" / "python3.12"
    uv = bin_dir / "uv"
    uv.write_text(
        f"""#!/usr/bin/env sh
printf '%s\\n' "$*" >> '{log}'
if [ "$1" = "venv" ]; then
    mkdir -p "$2/bin" '{system_python.parent}'
    cat > '{system_python}' <<'PY'
#!/usr/bin/env sh
exit 0
PY
    chmod +x '{system_python}'
    ln -s '{system_python}' "$2/bin/python"
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)

    returncode, _stdout, _stderr = _run_integration_script(
        tmp_path, shell_stubs, bin_dir, monkeypatch
    )

    uv_calls = log.read_text(encoding="utf-8").splitlines()
    sync_calls = [c for c in uv_calls if c.startswith("sync ")]
    pip_calls = [c for c in uv_calls if c.startswith("pip install ")]
    assert returncode == 0
    assert sync_calls, "uv sync must be called to install project deps"
    assert pip_calls, "uv pip install must be called to install tooling"
    expected_python = (tmp_path / ".venv-coverage" / "bin" / "python").absolute()
    resolved_python = system_python.resolve()

    sync_args = sync_calls[0].split()
    pip_args = pip_calls[0].split()
    sync_python = sync_args[sync_args.index("--python") + 1]
    pip_python = pip_args[pip_args.index("--python") + 1]

    assert sync_python == str(expected_python)
    assert pip_python == str(expected_python)
    assert sync_python != str(resolved_python)
    assert pip_python != str(resolved_python)
