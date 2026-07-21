"""Tests for the ``detect`` helper script."""

from __future__ import annotations

import importlib.util
import typing as typ
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "detect", Path(__file__).resolve().parents[1] / "scripts" / "detect.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
detect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(detect)


def _exit_code(exc: BaseException) -> int | None:
    """Return the exit code from Typer or SystemExit exceptions."""
    exit_code = getattr(exc, "exit_code", None)
    if exit_code is None:
        exit_code = getattr(exc, "code", None)
    return exit_code


@pytest.fixture
def setup_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> typ.Callable[
    [dict[str, str] | None, str | None],
    tuple[Path, Path],
]:
    """Return a factory that creates a project directory and output file."""

    def factory(
        files: dict[str, str] | None = None,
        initial_output: str | None = None,
    ) -> tuple[Path, Path]:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        for rel_path, content in (files or {}).items():
            target = project_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        monkeypatch.chdir(project_dir)
        out = project_dir / "gh.txt"
        if initial_output is None:
            out.touch()
        else:
            out.write_text(initial_output)
        return project_dir, out

    return factory


def test_invalid_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``detect.main`` exits with code 1 for an unknown format."""
    out = tmp_path / "gh.txt"
    with pytest.raises(detect.typer.Exit) as exc:
        detect.main("unknown", out)
    assert _exit_code(exc.value) == 1
    err = capsys.readouterr().err
    assert "Unsupported format" in err


@pytest.mark.parametrize(
    "fmt",
    ["lcov", "cobertura", "coveragepy", "LCOV"],
)
def test_valid_formats(
    fmt: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``detect.main`` accepts supported formats regardless of case."""
    out = tmp_path / "gh.txt"
    exc: detect.typer.Exit | None = None
    try:
        detect.main(fmt, out)
    except detect.typer.Exit as err:
        exc = err
    if fmt.lower() == "lcov":
        assert exc is not None
        assert _exit_code(exc) == 1
    else:
        assert exc is None
    err_msg = capsys.readouterr().err
    assert "Unsupported format" not in err_msg


def test_detect_writes_output_for_empty_file(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Valid format writes language and format to an empty ``GITHUB_OUTPUT`` file."""
    _project_dir, out = setup_project({"pyproject.toml": ""})

    detect.main("coveragepy", out)

    assert out.read_text() == "lang=python\nfmt=coveragepy\n"


def test_detect_appends_to_existing_output_file(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Existing ``GITHUB_OUTPUT`` contents are preserved when appending results."""
    _project_dir, out = setup_project({"pyproject.toml": ""}, "prev=1\n")

    detect.main("coveragepy", out)

    assert out.read_text() == "prev=1\nlang=python\nfmt=coveragepy\n"


def test_detect_appends_to_malformed_output_file(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Pre-existing malformed output lines remain untouched when appending results."""
    _project_dir, out = setup_project({"pyproject.toml": ""}, "garbage\nnot=kv\n")

    detect.main("coveragepy", out)

    assert out.read_text() == "garbage\nnot=kv\nlang=python\nfmt=coveragepy\n"


def test_detect_prefers_root_manifest_over_input(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Root ``Cargo.toml`` takes precedence over input ``cargo-manifest``."""
    _project_dir, out = setup_project(
        {
            "Cargo.toml": "",
            "nested/Cargo.toml": "",
        }
    )

    detect.main("lcov", out, "nested/Cargo.toml")

    assert out.read_text() == "lang=rust\nfmt=lcov\ncargo_manifest=Cargo.toml\n"


def test_detect_uses_input_manifest_when_root_missing(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Configured manifest is used when root manifest is absent."""
    _project_dir, out = setup_project({"rust-toy-app/Cargo.toml": ""})

    detect.main("lcov", out, "rust-toy-app/Cargo.toml")

    assert (
        out.read_text()
        == "lang=rust\nfmt=lcov\ncargo_manifest=rust-toy-app/Cargo.toml\n"
    )


def test_detect_uses_mixed_when_input_manifest_and_pyproject_exist(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Input manifest plus root Python project yields mixed language."""
    _project_dir, out = setup_project(
        {
            "crate/Cargo.toml": "",
            "pyproject.toml": "",
        }
    )

    detect.main("cobertura", out, "crate/Cargo.toml")

    assert (
        out.read_text()
        == "lang=mixed\nfmt=cobertura\ncargo_manifest=crate/Cargo.toml\n"
    )


def test_detect_ignores_missing_input_manifest_for_python_project(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """Missing ``cargo-manifest`` keeps Python-only behaviour."""
    _project_dir, out = setup_project({"pyproject.toml": ""})

    detect.main("coveragepy", out, "missing/Cargo.toml")

    assert out.read_text() == "lang=python\nfmt=coveragepy\n"


def test_detect_reads_manifest_from_env(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INPUT_CARGO_MANIFEST is used when no positional manifest is provided."""
    _project_dir, out = setup_project({"rust-toy-app/Cargo.toml": ""})
    monkeypatch.setenv("INPUT_CARGO_MANIFEST", "rust-toy-app/Cargo.toml")

    detect.main("lcov", out)

    assert (
        out.read_text()
        == "lang=rust\nfmt=lcov\ncargo_manifest=rust-toy-app/Cargo.toml\n"
    )


def test_detect_errors_when_only_missing_manifest_configured(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing configured manifest without Python/Rust roots should fail."""
    _project_dir, out = setup_project()

    with pytest.raises(detect.typer.Exit) as exc:
        detect.main("lcov", out, "missing/Cargo.toml")

    assert _exit_code(exc.value) == 1
    assert "Neither Cargo.toml nor pyproject.toml found" in capsys.readouterr().err


# A configuration-only pyproject.toml: tooling settings, no [project] table,
# and explicitly opted out of uv management. `auto` still treats it as Python.
_TOOLING_PYPROJECT = "[tool.ruff]\nline-length = 88\n\n[tool.uv]\nmanaged = false\n"
_REAL_PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'


def test_auto_preserves_mixed_detection(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """`language=auto` still classifies Cargo + any pyproject.toml as mixed."""
    _project_dir, out = setup_project(
        {"Cargo.toml": "", "pyproject.toml": _TOOLING_PYPROJECT}
    )

    detect.main("cobertura", out, "", "auto")

    assert out.read_text() == "lang=mixed\nfmt=cobertura\ncargo_manifest=Cargo.toml\n"


def test_forced_rust_ignores_tooling_pyproject(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """`language=rust` emits `lang=rust` with lcov despite a config-only pyproject."""
    _project_dir, out = setup_project(
        {"Cargo.toml": "", "pyproject.toml": _TOOLING_PYPROJECT}
    )

    detect.main("lcov", out, "", "rust")

    assert out.read_text() == "lang=rust\nfmt=lcov\ncargo_manifest=Cargo.toml\n"


def test_forced_rust_reads_language_from_env(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INPUT_LANGUAGE forces the scope when no positional language is provided."""
    _project_dir, out = setup_project(
        {"Cargo.toml": "", "pyproject.toml": _TOOLING_PYPROJECT}
    )
    monkeypatch.setenv("INPUT_LANGUAGE", "rust")

    detect.main("lcov", out)

    assert out.read_text() == "lang=rust\nfmt=lcov\ncargo_manifest=Cargo.toml\n"


def test_forced_python_accepts_real_project(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """`language=python` succeeds for a syncable pyproject with a [project] table."""
    _project_dir, out = setup_project({"pyproject.toml": _REAL_PYPROJECT})

    detect.main("coveragepy", out, "", "python")

    assert out.read_text() == "lang=python\nfmt=coveragepy\n"


def test_forced_mixed_accepts_both(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
) -> None:
    """`language=mixed` succeeds when both prerequisites are satisfied."""
    _project_dir, out = setup_project(
        {"crate/Cargo.toml": "", "pyproject.toml": _REAL_PYPROJECT}
    )

    detect.main("cobertura", out, "crate/Cargo.toml", "mixed")

    assert (
        out.read_text()
        == "lang=mixed\nfmt=cobertura\ncargo_manifest=crate/Cargo.toml\n"
    )


class _ErrorCase(typ.NamedTuple):
    """A forced-language failure scenario and its expected stderr fragment."""

    files: dict[str, str]
    fmt: str
    language: str
    expected_error: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _ErrorCase(
                {"pyproject.toml": _REAL_PYPROJECT},
                "lcov",
                "rust",
                "language=rust requires a Cargo manifest",
            ),
            id="rust-without-manifest",
        ),
        pytest.param(
            _ErrorCase(
                {"pyproject.toml": _TOOLING_PYPROJECT},
                "coveragepy",
                "python",
                "language=python requires",
            ),
            id="python-with-tooling-only-pyproject",
        ),
        pytest.param(
            _ErrorCase(
                {"pyproject.toml": _REAL_PYPROJECT},
                "cobertura",
                "mixed",
                "language=mixed requires a Cargo manifest",
            ),
            id="mixed-without-manifest",
        ),
        pytest.param(
            _ErrorCase(
                {"Cargo.toml": "", "pyproject.toml": _TOOLING_PYPROJECT},
                "cobertura",
                "mixed",
                "syncable pyproject.toml",
            ),
            id="mixed-with-tooling-only-pyproject",
        ),
        pytest.param(
            _ErrorCase({"Cargo.toml": ""}, "lcov", "elvish", "Unsupported language"),
            id="invalid-language-value",
        ),
    ],
)
def test_forced_language_errors(
    setup_project: typ.Callable[[dict[str, str] | None, str | None], tuple[Path, Path]],
    capsys: pytest.CaptureFixture[str],
    case: _ErrorCase,
) -> None:
    """Forced-language modes fail fast when prerequisites are unmet or invalid."""
    _project_dir, out = setup_project(case.files)

    with pytest.raises(detect.typer.Exit) as exc:
        detect.main(case.fmt, out, "", case.language)

    assert _exit_code(exc.value) == 1
    assert case.expected_error in capsys.readouterr().err
