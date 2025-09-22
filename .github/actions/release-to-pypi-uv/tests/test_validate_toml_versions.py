"""Tests for validate_toml_versions.py."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from pathlib import Path
    from types import ModuleType

import pytest
from typer.testing import CliRunner

from ._helpers import load_script_module


@pytest.fixture(name="module")
def fixture_module() -> ModuleType:
    """Load the ``validate_toml_versions`` script module under test."""
    return load_script_module("validate_toml_versions")


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary directory as the working tree for each test."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_pyproject(base: Path, content: str) -> None:
    """Create a ``pyproject.toml`` file populated with the provided content."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "pyproject.toml").write_text(content.strip())


def _invoke_main(module: ModuleType, **kwargs: object) -> None:
    """Invoke ``module.main`` with defaults tailored for the tests."""
    kwargs.setdefault("pattern", "**/pyproject.toml")
    kwargs.setdefault("fail_on_dynamic", "false")
    module.main(**kwargs)


def test_passes_when_versions_match(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Succeed when all discovered packages match the expected version."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
version = "1.0.0"
""",
    )

    _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert (
        captured.out.strip()
        == "Checked 1 PEP 621 project file(s); all versions match 1.0.0."
    )


def test_cli_defaults_when_optional_parameters_omitted(
    project_root: Path, module: ModuleType
) -> None:
    """Use default CLI values when optional flags are not provided."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
version = "1.0.0"
""",
    )

    runner = CliRunner()
    app = module.typer.Typer()
    app.command()(module.main)
    result = runner.invoke(app, ["--version", "1.0.0"])

    assert result.exit_code == 0
    assert "all versions match 1.0.0" in result.output


def test_fails_on_mismatch(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail when a package declares a version that differs from the tag."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
version = "1.0.1"
""",
    )

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert "version '1.0.1' != tag version '1.0.0'" in captured.err


def test_dynamic_version_failure(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail when dynamic versions are disallowed but present in metadata."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0", fail_on_dynamic="true")

    captured = capsys.readouterr()
    assert "dynamic 'version'" in captured.err


@pytest.mark.parametrize("truthy", ["true", "TRUE", "Yes", " y ", "1", "On"])
def test_dynamic_version_failure_for_truthy_variants(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    truthy: str,
) -> None:
    """Fail whenever dynamic versions are disallowed with truthy inputs."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0", fail_on_dynamic=truthy)

    captured = capsys.readouterr()
    assert "dynamic 'version'" in captured.err


def test_fails_on_parse_error(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail gracefully when the TOML configuration cannot be parsed."""
    target = project_root / "pkg"
    target.mkdir()
    (target / "pyproject.toml").write_text("this is not TOML")

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert "failed to parse" in captured.err


def test_dynamic_version_allowed_when_flag_false(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow dynamic versions when the flag explicitly disables failures."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    _invoke_main(module, version="1.0.0", fail_on_dynamic="false")

    captured = capsys.readouterr()
    assert "uses dynamic 'version'" in captured.out


@pytest.mark.parametrize("falsey", ["false", "", "no", "0", "off", "n", "False"])
def test_dynamic_version_allowed_for_falsey_variants(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    falsey: str,
) -> None:
    """Allow dynamic versions for all supported falsey flag values."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    _invoke_main(module, version="1.0.0", fail_on_dynamic=falsey)

    captured = capsys.readouterr()
    assert "uses dynamic 'version'" in captured.out


def test_skips_files_in_ignored_directories(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warn and exit when only ignored directories match the pattern."""
    ignored = project_root / ".venv" / "pkg"
    _write_pyproject(
        ignored,
        """
[project]
name = "ignored"
version = "9.9.9"
""",
    )
    _invoke_main(module, version="1.0.0")
    captured = capsys.readouterr()
    assert "::warning::No TOML files matched pattern" in captured.out


def test_dynamic_version_allowed_when_flag_unset(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow dynamic versions when the flag is omitted entirely."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert "uses dynamic 'version'" in captured.out


def test_missing_project_section_is_ignored(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ignore files lacking a ``[project]`` table when validating versions."""
    _write_pyproject(
        project_root / "pkg",
        """
[tool.poetry]
name = "demo"
version = "1.0.0"
""",
    )

    _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_fails_when_project_version_missing(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Error when a project lacks a version and is not marked dynamic."""
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
""",
    )
    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0")
    captured = capsys.readouterr()
    assert "missing [project].version" in captured.err


def test_multiple_toml_files_mixed_validity(
    project_root: Path,
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail when any discovered TOML file contains a mismatched version."""
    _write_pyproject(
        project_root / "pkg_valid",
        """
[project]
name = "demo"
version = "1.0.0"
""",
    )
    _write_pyproject(
        project_root / "pkg_invalid",
        """
[project]
name = "demo"
version = "2.0.0"
""",
    )

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert "!= tag version" in captured.err


def test_iter_files_discovers_paths_in_deterministic_order(
    project_root: Path,
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure TOML discovery yields paths in a stable, sorted order."""
    _write_pyproject(
        project_root / "pkg_b",
        """
[project]
name = "pkg-b"
version = "1.0.0"
""",
    )
    _write_pyproject(
        project_root / "pkg_a",
        """
[project]
name = "pkg-a"
version = "1.0.0"
""",
    )

    first = project_root / "pkg_a" / "pyproject.toml"
    second = project_root / "pkg_b" / "pyproject.toml"

    def fake_glob(
        self: Path,
        pattern: str,
    ) -> typ.Iterator[Path]:
        _ = self
        assert pattern == "**/pyproject.toml"
        return iter((second, first))

    monkeypatch.setattr(module.Path, "glob", fake_glob, raising=False)

    discovered = list(module._iter_files("**/pyproject.toml"))

    assert discovered == [first, second]


def test_iter_files_discovers_paths_in_sorted_order(
    project_root: Path,
    module: ModuleType,
) -> None:
    """Ensure discovery order remains deterministic for reproducible output."""
    for name in ("pkg_c", "pkg_a", "pkg_b"):
        _write_pyproject(
            project_root / name,
            """
[project]
name = "demo"
version = "1.0.0"
""",
        )

    discovered = list(module._iter_files("**/pyproject.toml"))
    relative = [path.as_posix() for path in discovered]
    assert relative == sorted(relative)


@pytest.mark.parametrize("value", ["true", "TRUE", "Yes", "1", "on"])
def test_parse_bool_truthy_values(module: ModuleType, value: str) -> None:
    """Treat recognised truthy values as ``True`` for configuration flags."""
    assert module._parse_bool(value) is True


@pytest.mark.parametrize("value", [None, "", "false", "no", "0", "off", "n"])
def test_parse_bool_falsey_values(module: ModuleType, value: str | None) -> None:
    """Treat recognised falsey values as ``False`` for configuration flags."""
    assert module._parse_bool(value) is False
