"""Tests for validate_toml_versions.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ._helpers import load_script_module

@pytest.fixture(name="module")
def fixture_module() -> Any:
    return load_script_module("validate_toml_versions")


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_pyproject(base: Path, content: str) -> None:
    base.mkdir()
    (base / "pyproject.toml").write_text(content.strip())


def _invoke_main(module: Any, **kwargs: Any) -> None:
    kwargs.setdefault("pattern", "**/pyproject.toml")
    kwargs.setdefault("fail_on_dynamic", "false")
    module.main(**kwargs)


def test_passes_when_versions_match(project_root: Path, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert "all versions match 1.0.0" in captured.out


def test_fails_on_mismatch(project_root: Path, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
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


def test_dynamic_version_failure(project_root: Path, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
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
    module: Any,
    capsys: pytest.CaptureFixture[str],
    truthy: str,
) -> None:
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


def test_fails_on_parse_error(project_root: Path, module: Any, capsys: pytest.CaptureFixture[str]) -> None:
    target = project_root / "pkg"
    target.mkdir()
    (target / "pyproject.toml").write_text("this is not TOML")

    with pytest.raises(module.typer.Exit):
        _invoke_main(module, version="1.0.0")

    captured = capsys.readouterr()
    assert "failed to parse" in captured.err


def test_dynamic_version_allowed_when_flag_false(
    project_root: Path,
    module: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    module: Any,
    capsys: pytest.CaptureFixture[str],
    falsey: str,
) -> None:
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


def test_dynamic_version_allowed_when_flag_unset(
    project_root: Path,
    module: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_pyproject(
        project_root / "pkg",
        """
[project]
name = "demo"
dynamic = ["version"]
""",
    )

    _invoke_main(module, version="1.0.0", fail_on_dynamic="")

    captured = capsys.readouterr()
    assert "uses dynamic 'version'" in captured.out


def test_missing_project_section_is_ignored(
    project_root: Path,
    module: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


def test_multiple_toml_files_mixed_validity(
    project_root: Path,
    module: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


@pytest.mark.parametrize("value", ["true", "TRUE", "Yes", "1", "on"])
def test_parse_bool_truthy_values(module: Any, value: str) -> None:
    assert module._parse_bool(value) is True


@pytest.mark.parametrize("value", [None, "", "false", "no", "0", "off", "n"])
def test_parse_bool_falsey_values(module: Any, value: str | None) -> None:
    assert module._parse_bool(value) is False
