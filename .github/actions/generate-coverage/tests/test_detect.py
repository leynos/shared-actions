"""Tests for the ``detect`` helper script."""

from __future__ import annotations

import importlib.util
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid format writes language and format to an empty ``GITHUB_OUTPUT`` file."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"

    detect.main("coveragepy", out)

    assert out.read_text() == "lang=python\nfmt=coveragepy\n"


def test_detect_appends_to_existing_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing ``GITHUB_OUTPUT`` contents are preserved when appending results."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"
    out.write_text("prev=1\n")

    detect.main("coveragepy", out)

    assert out.read_text() == "prev=1\nlang=python\nfmt=coveragepy\n"


def test_detect_appends_to_malformed_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing malformed output lines remain untouched when appending results."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"
    out.write_text("garbage\nnot=kv\n")

    detect.main("coveragepy", out)

    assert out.read_text() == "garbage\nnot=kv\nlang=python\nfmt=coveragepy\n"


def test_detect_prefers_root_manifest_over_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root ``Cargo.toml`` takes precedence over input ``cargo-manifest``."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "Cargo.toml").write_text("")
    nested_manifest = project_dir / "nested" / "Cargo.toml"
    nested_manifest.parent.mkdir(parents=True, exist_ok=True)
    nested_manifest.write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"

    detect.main("lcov", out, "nested/Cargo.toml")

    assert out.read_text() == "lang=rust\nfmt=lcov\ncargo_manifest=Cargo.toml\n"


def test_detect_uses_input_manifest_when_root_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured manifest is used when root manifest is absent."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    nested_manifest = project_dir / "rust-toy-app" / "Cargo.toml"
    nested_manifest.parent.mkdir(parents=True, exist_ok=True)
    nested_manifest.write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"

    detect.main("lcov", out, "rust-toy-app/Cargo.toml")

    assert (
        out.read_text()
        == "lang=rust\nfmt=lcov\ncargo_manifest=rust-toy-app/Cargo.toml\n"
    )


def test_detect_uses_mixed_when_input_manifest_and_pyproject_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Input manifest plus root Python project yields mixed language."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    nested_manifest = project_dir / "crate" / "Cargo.toml"
    nested_manifest.parent.mkdir(parents=True, exist_ok=True)
    nested_manifest.write_text("")
    (project_dir / "pyproject.toml").write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"

    detect.main("cobertura", out, "crate/Cargo.toml")

    assert (
        out.read_text()
        == "lang=mixed\nfmt=cobertura\ncargo_manifest=crate/Cargo.toml\n"
    )


def test_detect_ignores_missing_input_manifest_for_python_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ``cargo-manifest`` keeps Python-only behaviour."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("")
    monkeypatch.chdir(project_dir)
    out = project_dir / "gh.txt"

    detect.main("coveragepy", out, "missing/Cargo.toml")

    assert out.read_text() == "lang=python\nfmt=coveragepy\n"
