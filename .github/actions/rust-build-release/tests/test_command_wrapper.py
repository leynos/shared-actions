"""Tests for command wrapper formatting."""

from __future__ import annotations

import shlex
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType


class _DummyCommand:
    def __init__(self, parts: list[object]) -> None:
        self._parts = parts

    def formulate(self) -> list[object]:
        return list(self._parts)


def test_command_wrapper_str_uses_display_name(main_module: ModuleType) -> None:
    """__str__ should reflect the display name and the formulated args."""
    parts: list[object] = [Path("/usr/bin/cross"), "+1.89.0", "build"]
    wrapper = main_module._CommandWrapper(_DummyCommand(parts), "cross")

    expected = shlex.join(["cross", "+1.89.0", "build"])
    assert str(wrapper) == expected


def test_command_wrapper_str_quotes_special_characters(
    main_module: ModuleType,
) -> None:
    """__str__ should quote parts that include spaces or shell characters."""
    parts: list[object] = [
        "/usr/bin/cargo",
        "arg with space",
        "semi;colon",
        "weird$var",
    ]
    wrapper = main_module._CommandWrapper(_DummyCommand(parts), "cargo")

    rendered = str(wrapper)
    expected = shlex.join(["cargo", "arg with space", "semi;colon", "weird$var"])
    assert rendered == expected
    assert "'arg with space'" in rendered
    assert "'semi;colon'" in rendered
    assert "'weird$var'" in rendered


def test_command_wrapper_str_coerces_non_string_parts(
    main_module: ModuleType,
    tmp_path: Path,
) -> None:
    """__str__ should coerce non-string formulate values using str()."""
    path = tmp_path / "path with space" / "bin"
    parts: list[object] = [path, Path("nested dir/file.txt"), 42]
    wrapper = main_module._CommandWrapper(_DummyCommand(parts), "run")

    expected = shlex.join([str(value) for value in ["run", *parts[1:]]])
    assert str(wrapper) == expected
