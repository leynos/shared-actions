"""Tests for the component package creation helper."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_build_component_invokes_pkgbuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Invoke ``pkgbuild`` and write the component package."""
    module = load_module("build_component")
    monkeypatch.chdir(tmp_path)

    work_dir = tmp_path / ".macos-package"
    pkgroot = work_dir / "pkgroot"
    pkgroot.mkdir(parents=True, exist_ok=True)

    class FakeCommand:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.output: Path | None = None

        def __getitem__(self, args: tuple[str, ...]) -> FakeCommand:
            self.calls.append(list(args))
            self.output = Path(args[-1])
            return self

        def __call__(self) -> None:
            if self.output is None:
                pytest.fail("pkgbuild command invoked without an output path")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text("component", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(module, "local", {"pkgbuild": fake})

    module.main(identifier="com.example.tool", version="1.2.3", name="tool")

    component = work_dir / "build/tool-1.2.3-component.pkg"
    assert component.read_text(encoding="utf-8") == "component"
    assert fake.calls
    assert fake.calls[0][0] == "--identifier"


def test_build_component_requires_pkgroot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Raise an error when the staged pkgroot directory is missing."""
    module = load_module("build_component")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(module.ActionError):
        module.main(identifier="com.example.tool", version="1.0.0", name="tool")
