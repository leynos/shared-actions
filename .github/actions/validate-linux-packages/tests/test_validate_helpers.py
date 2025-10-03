"""Tests for validate_helpers utilities."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_helpers.py"


@pytest.fixture(scope="module")
def validate_helpers_module() -> object:
    """Load the validate_helpers module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_helpers")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_helpers", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_helpers module"
        raise RuntimeError(message)

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validation_error() -> type[Exception]:
    """Return the ValidationError class for helper tests."""
    spec = importlib.util.spec_from_file_location(
        "validate_exceptions", SCRIPTS_DIR / "validate_exceptions.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_exceptions module"
        raise RuntimeError(message)
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module.ValidationError


def test_ensure_directory_creates_path(
    validate_helpers_module: object,
    tmp_path: Path,
) -> None:
    """ensure_directory creates the requested directory and returns it."""
    target = tmp_path / "nested" / "dir"

    result = validate_helpers_module.ensure_directory(target)

    assert result == target
    assert target.is_dir()


def test_ensure_exists_raises_when_missing(
    validate_helpers_module: object,
    validation_error: type[Exception],
    tmp_path: Path,
) -> None:
    """ensure_exists raises ValidationError when the path is absent."""
    candidate = tmp_path / "missing"

    with pytest.raises(validation_error, match="package directory not found"):
        validate_helpers_module.ensure_exists(candidate, "package directory not found")


def test_get_command_returns_stub(
    validate_helpers_module: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_command retrieves commands from the plumbum local mapping."""
    module = validate_helpers_module

    class _Local(dict):
        def __getitem__(self, key: str) -> object:
            if key not in self:
                raise KeyError(key)
            return super().__getitem__(key)

    fake_local = _Local({"echo": object()})
    monkeypatch.setattr(module, "local", fake_local)

    result = module.get_command("echo")
    assert result is fake_local["echo"]

    with pytest.raises(module.ValidationError, match="required command not found"):
        module.get_command("missing")


def test_unique_match_validates_single_entry(
    validate_helpers_module: object,
    validation_error: type[Exception],
    tmp_path: Path,
) -> None:
    """unique_match returns the sole entry and errors otherwise."""
    path = tmp_path / "file.txt"
    path.touch()

    result = validate_helpers_module.unique_match([path], description="payload")
    assert result == path

    with pytest.raises(validation_error, match="expected exactly one payload"):
        validate_helpers_module.unique_match([], description="payload")

    with pytest.raises(validation_error, match="expected exactly one payload"):
        validate_helpers_module.unique_match([path, path], description="payload")
