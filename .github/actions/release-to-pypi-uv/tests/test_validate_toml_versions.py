"""Tests for validate_toml_versions.py."""

from __future__ import annotations

import typing as typ
from types import ModuleType

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from pathlib import Path

import pytest

from ._helpers import load_script_module


@pytest.fixture(name="module")
def fixture_module() -> ModuleType:
    """Load the ``validate_toml_versions`` script module for testing.

    Returns
    -------
    ModuleType
        Imported script module under test.
    """

    return load_script_module("validate_toml_versions")


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary directory as the working tree for each test.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory provided by pytest.
    monkeypatch : pytest.MonkeyPatch
        Fixture used to update the working directory during the test.

    Returns
    -------
    Path
        Path to the temporary working tree for the current test.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_pyproject(base: Path, content: str) -> None:
    """Create a ``pyproject.toml`` file populated with the provided content.

    Parameters
    ----------
    base : Path
        Directory that should receive the generated ``pyproject.toml`` file.
    content : str
        TOML content to write to the project configuration file.
    """

    base.mkdir()
    (base / "pyproject.toml").write_text(content.strip())


def _invoke_main(module: ModuleType, **kwargs: str) -> None:
    """Invoke ``module.main`` with defaults tailored for the tests.

    Parameters
    ----------
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    **kwargs : str
        Additional keyword arguments forwarded to ``module.main``.
    """

    kwargs.setdefault("pattern", "**/pyproject.toml")
    kwargs.setdefault("fail_on_dynamic", "false")
    module.main(**kwargs)


def test_passes_when_versions_match(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Succeed when all discovered packages match the expected version.

    Parameters
    ----------
    project_root : Path
        Temporary project root under validation.
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    capsys : pytest.CaptureFixture[str]
        Captures stdout emitted during validation.
    """

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


def test_fails_on_mismatch(
    project_root: Path, module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail when a package declares a version that differs from the tag.

    Parameters
    ----------
    project_root : Path
        Temporary project root under validation.
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    capsys : pytest.CaptureFixture[str]
        Captures stderr emitted during validation.
    """

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
    """Fail when dynamic versions are disallowed but present in metadata.

    Parameters
    ----------
    project_root : Path
        Temporary project root under validation.
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    capsys : pytest.CaptureFixture[str]
        Captures stderr emitted during validation.
    """

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
    """Fail whenever dynamic versions are disallowed with truthy inputs.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    truthy : str
        Variant of the ``fail_on_dynamic`` flag expected to trigger a failure.
    """
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
    """Fail gracefully when the TOML configuration cannot be parsed.

    Parameters
    ----------
    project_root : Path
        Temporary project root under validation.
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    capsys : pytest.CaptureFixture[str]
        Captures stderr emitted during validation.
    """

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
    """Allow dynamic versions when the flag explicitly disables failures.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    """
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
    """Allow dynamic versions for all supported falsey flag values.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    falsey : str
        Representation of ``fail_on_dynamic`` that should be treated as false.
    """
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
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow dynamic versions when the flag is omitted entirely.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    """
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
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ignore files lacking a ``[project]`` table when validating versions.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    """
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
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail when any discovered TOML file contains a mismatched version.

    Parameters
    ----------
    project_root : Path
        Temporary project directory for the test run.
    module : Any
        Script module under test.
    capsys : pytest.CaptureFixture[str]
        Captures output from the command execution.
    """
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
def test_parse_bool_truthy_values(module: ModuleType, value: str) -> None:
    """Treat recognised truthy values as ``True`` for configuration flags.

    Parameters
    ----------
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    value : str
        String representation expected to evaluate to ``True``.
    """

    assert module._parse_bool(value) is True


@pytest.mark.parametrize("value", [None, "", "false", "no", "0", "off", "n"])
def test_parse_bool_falsey_values(module: ModuleType, value: str | None) -> None:
    """Treat recognised falsey values as ``False`` for configuration flags.

    Parameters
    ----------
    module : ModuleType
        Loaded ``validate_toml_versions`` script module.
    value : str or None
        Representation expected to evaluate to ``False``.
    """

    assert module._parse_bool(value) is False
