"""Tests for the ``export-cargo-metadata`` read_manifest script."""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "read_manifest.py"
SCRIPT_DIR = MODULE_PATH.parent
SCRIPT_DIR_STR = str(SCRIPT_DIR)
if SCRIPT_DIR_STR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR_STR)

# Add repository root for cargo_utils import
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location("read_manifest_module", MODULE_PATH)
if spec is None or spec.loader is None:  # pragma: no cover - defensive import guard
    message = "Unable to load read_manifest module for testing"
    raise RuntimeError(message)
module = importlib.util.module_from_spec(spec)
if not isinstance(module, ModuleType):  # pragma: no cover - importlib contract
    message = "module_from_spec did not return a ModuleType"
    raise TypeError(message)
sys.modules[spec.name] = module
spec.loader.exec_module(module)  # type: ignore[misc]
read_manifest_mod = module

if typ.TYPE_CHECKING:
    from pathlib import Path as PathType


def _write_manifest(path: PathType, content: str) -> None:
    """Write a manifest with content to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("On", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("FALSE", False),
        ("No", False),
        ("0", False),
        ("off", False),
        ("OFF", False),
        ("", False),
    ],
)
def test_coerce_bool_accepts_expected_inputs(
    value: bool | str,  # noqa: FBT001
    expected: bool,  # noqa: FBT001
) -> None:
    """Ensure _coerce_bool recognises accepted truthy and falsey values."""
    assert (
        read_manifest_mod._coerce_bool(value=value, parameter="export-to-env")
        is expected
    )


def test_coerce_bool_rejects_invalid_values() -> None:
    """Invalid values should raise an informative ValueError."""
    with pytest.raises(ValueError, match="boolean-like"):
        read_manifest_mod._coerce_bool(value="not-a-boolean", parameter="export-to-env")


def _load_and_extract(
    tmp_path: PathType, manifest_content: str, field: str
) -> str | None:
    """Write a manifest, load it, and extract a field."""
    from cargo_utils import read_manifest

    manifest_path = tmp_path / "Cargo.toml"
    _write_manifest(manifest_path, manifest_content)
    manifest = read_manifest(manifest_path)
    return read_manifest_mod._extract_field(manifest, manifest_path, field)


@pytest.mark.parametrize(
    ("manifest_content", "field", "expected"),
    [
        pytest.param(
            '[package]\nname = "test-pkg"\nversion = "1.0.0"\n',
            "name",
            "test-pkg",
            id="name_field",
        ),
        pytest.param(
            '[package]\nname = "test-pkg"\nversion = "2.3.4"\n',
            "version",
            "2.3.4",
            id="version_field",
        ),
        pytest.param(
            '[package]\nname = "my-lib"\nversion = "1.0.0"\n\n'
            '[[bin]]\nname = "my-cli"\npath = "src/main.rs"\n',
            "bin-name",
            "my-cli",
            id="bin_name_from_bin_section",
        ),
        pytest.param(
            '[package]\nname = "fallback-pkg"\nversion = "1.0.0"\n',
            "bin-name",
            "fallback-pkg",
            id="bin_name_from_package",
        ),
        pytest.param(
            '[package]\nname = "test-pkg"\nversion = "1.0.0"\n'
            'description = "A test package"\n',
            "description",
            "A test package",
            id="description_present",
        ),
        pytest.param(
            '[package]\nname = "test-pkg"\nversion = "1.0.0"\n',
            "description",
            None,
            id="description_missing",
        ),
        pytest.param(
            '[package]\nname = "test-pkg"\nversion = "1.0.0"\n',
            "unknown-field",
            None,
            id="unknown_field",
        ),
    ],
)
def test_extract_field(
    tmp_path: PathType, manifest_content: str, field: str, expected: str | None
) -> None:
    """Verify _extract_field handles various manifest formats and field types."""
    result = _load_and_extract(tmp_path, manifest_content, field)
    assert result == expected


def _setup_github_env(
    monkeypatch: pytest.MonkeyPatch, workspace: PathType
) -> tuple[PathType, PathType]:
    """Set up GITHUB_* environment variables and return output/env file paths."""
    output_file = workspace / "outputs"
    env_file = workspace / "env"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    return output_file, env_file


def test_main_exports_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: PathType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The main function should write outputs to GITHUB_OUTPUT."""
    _write_manifest(
        tmp_path / "Cargo.toml",
        '[package]\nname = "demo-pkg"\nversion = "3.4.5"\n',
    )
    output_file, _ = _setup_github_env(monkeypatch, tmp_path)

    read_manifest_mod.main(
        manifest_path="Cargo.toml",
        fields="name,version",
        export_to_env="false",
    )

    contents = output_file.read_text(encoding="utf-8").splitlines()
    assert "name=demo-pkg" in contents
    assert "version=3.4.5" in contents

    captured = capsys.readouterr()
    assert "Exported:" in captured.out


def test_main_exports_to_env_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: PathType,
) -> None:
    """The main function should write to GITHUB_ENV when enabled."""
    _write_manifest(
        tmp_path / "Cargo.toml",
        '[package]\nname = "env-pkg"\nversion = "1.2.3"\n',
    )
    _, env_file = _setup_github_env(monkeypatch, tmp_path)

    read_manifest_mod.main(
        manifest_path="Cargo.toml",
        fields="name,version",
        export_to_env="true",
    )

    env_contents = env_file.read_text(encoding="utf-8").splitlines()
    assert "NAME=env-pkg" in env_contents
    assert "VERSION=1.2.3" in env_contents


def test_main_handles_missing_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: PathType,
) -> None:
    """The main function should fail gracefully for missing manifests."""
    _setup_github_env(monkeypatch, tmp_path)

    recorded_errors: list[tuple[str, str]] = []

    def record_error(title: str, message: str, *, path: PathType | None = None) -> None:
        recorded_errors.append((title, message))

    monkeypatch.setattr(read_manifest_mod, "_emit_error", record_error)

    with pytest.raises(SystemExit) as excinfo:
        read_manifest_mod.main(
            manifest_path="Cargo.toml",
            fields="name,version",
            export_to_env="false",
        )

    assert excinfo.value.code == 1
    assert recorded_errors
    first_title, _ = recorded_errors[0]
    assert "read failure" in first_title


def test_main_handles_invalid_export_to_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: PathType,
) -> None:
    """The main function should reject invalid export-to-env values."""
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_manifest(
        manifest_path,
        '[package]\nname = "test"\nversion = "1.0.0"\n',
    )

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

    recorded_errors: list[tuple[str, str]] = []

    def record_error(title: str, message: str, *, path: PathType | None = None) -> None:
        recorded_errors.append((title, message))

    monkeypatch.setattr(read_manifest_mod, "_emit_error", record_error)

    with pytest.raises(SystemExit) as excinfo:
        read_manifest_mod.main(
            manifest_path="Cargo.toml",
            fields="name,version",
            export_to_env="invalid-bool",
        )

    assert excinfo.value.code == 1
    assert recorded_errors
    first_title, first_message = recorded_errors[0]
    assert first_title == "Invalid input"
    assert "invalid-bool" in first_message
