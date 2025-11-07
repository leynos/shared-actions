"""Tests for the shared macOS packaging helper utilities."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_action_work_dir_creates_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Create the working directory underneath the current workspace."""
    utils = load_module("_utils")
    monkeypatch.chdir(tmp_path)

    work_dir = utils.action_work_dir()

    assert work_dir == tmp_path / ".macos-package"
    assert work_dir.is_dir()


def test_append_and_write_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Append key/value pairs to environment and output files."""
    utils = load_module("_utils")
    env_file, output_file = tmp_path / "env", tmp_path / "output"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    utils.append_key_value(env_file, "KEY", "VALUE")
    utils.write_env("FOO", "BAR")
    utils.write_output("baz", "qux")

    assert env_file.read_text(encoding="utf-8") == "KEY=VALUE\nFOO=BAR\n"
    assert output_file.read_text(encoding="utf-8") == "baz=qux\n"


def test_configure_app_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Configure a Cyclopts app that reads GitHub inputs from the env."""
    utils = load_module("_utils")
    captured: list[str] = []

    app = utils.configure_app()

    @app.default
    def _main(*, value: str) -> None:
        captured.append(value)

    monkeypatch.setenv("INPUT_VALUE", "example")
    utils.run_app(app)

    assert captured == ["example"]
    out = capsys.readouterr()
    assert out.err == ""


def test_run_app_reports_action_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Handle ``ActionError`` exceptions with a consistent exit code."""
    utils = load_module("_utils")
    app = utils.configure_app()

    @app.default
    def _main() -> None:
        message = "boom"
        raise utils.ActionError(message)

    with pytest.raises(SystemExit) as excinfo:
        utils.run_app(app)

    assert excinfo.value.code == 1
    assert "boom" in capsys.readouterr().err


def test_run_app_honours_explicit_arguments(
    load_module: cabc.Callable[[str], object],
) -> None:
    """Explicit ``argv`` entries are forwarded to the Cyclopts app."""
    utils = load_module("_utils")
    captured: list[str] = []

    app = utils.configure_app()

    @app.default
    def _main(*, value: str) -> None:
        captured.append(value)

    utils.run_app(app, argv=["--value", "from-argv"])

    assert captured == ["from-argv"]


def test_ensure_regular_file_accepts_file(
    tmp_path: Path,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Return the resolved path when a regular file exists."""
    utils = load_module("_utils")
    file_path = tmp_path / "example.txt"
    file_path.write_text("data", encoding="utf-8")

    resolved = utils.ensure_regular_file(file_path, "Example file")

    assert resolved == file_path.resolve()


@pytest.mark.parametrize(
    "factory",
    [
        lambda tmp_path: tmp_path / "missing.txt",
        lambda tmp_path: tmp_path / "dir",
    ],
)
def test_ensure_regular_file_rejects_invalid_paths(
    tmp_path: Path,
    factory: cabc.Callable[[Path], Path],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Raise ``ActionError`` for missing files or directories."""
    utils = load_module("_utils")
    path = factory(tmp_path)
    if path.suffix == "":
        path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(utils.ActionError):
        utils.ensure_regular_file(path, "Example file")


def test_remove_file_logs_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Log a warning when removing an existing file fails."""
    utils = load_module("_utils")
    target = tmp_path / "artefact.txt"
    target.write_text("payload", encoding="utf-8")

    original_unlink = Path.unlink

    def _raise(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            reason = PermissionError("denied")
            raise reason
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _raise)
    utils.remove_file(target, context="test artefact")

    assert (
        "Warning: Could not remove existing test artefact: denied"
        in capsys.readouterr().err
    )
