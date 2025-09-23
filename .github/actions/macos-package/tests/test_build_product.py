"""Tests for the product archive creation helper."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def _make_component(work_dir: Path, name: str, version: str) -> Path:
    """Create a stub component package in ``work_dir``."""
    build_dir = work_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    component = build_dir / f"{name}-{version}-component.pkg"
    component.write_text("component", encoding="utf-8")
    return component


def test_build_product_wraps_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Wrap a component package with ``productbuild``."""
    module = load_module("build_product")
    monkeypatch.chdir(tmp_path)

    work_dir = tmp_path / ".macos-package"
    _make_component(work_dir, "tool", "1.0.0")

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
                pytest.fail("productbuild invoked without an output path")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text("product", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(module, "local", {"productbuild": fake})

    module.main(name="tool", version="1.0.0", include_license_panel=False)

    output_pkg = tmp_path / "dist/tool-1.0.0.pkg"
    assert output_pkg.read_text(encoding="utf-8") == "product"
    assert fake.calls
    assert fake.calls[0][0] == "--package"


def test_build_product_with_license_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Include Distribution XML and resources when requested."""
    module = load_module("build_product")
    monkeypatch.chdir(tmp_path)

    work_dir = tmp_path / ".macos-package"
    _make_component(work_dir, "tool", "1.0.0")
    (work_dir / "dist.xml").write_text("<xml />\n", encoding="utf-8")
    resources = work_dir / "Resources"
    resources.mkdir(parents=True, exist_ok=True)

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
                pytest.fail("productbuild invoked without an output path")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text("product", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(module, "local", {"productbuild": fake})

    module.main(name="tool", version="1.0.0", include_license_panel=True)

    output_pkg = tmp_path / "dist/tool-1.0.0.pkg"
    assert output_pkg.read_text(encoding="utf-8") == "product"
    assert fake.calls
    assert fake.calls[0][0] == "--distribution"


def test_build_product_requires_license_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Require Distribution XML and resources when licence panel is enabled."""
    module = load_module("build_product")
    monkeypatch.chdir(tmp_path)

    work_dir = tmp_path / ".macos-package"
    _make_component(work_dir, "tool", "1.0.0")

    with pytest.raises(module.ActionError):
        module.main(name="tool", version="1.0.0", include_license_panel=True)


def test_build_product_warns_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Log a warning when the existing product archive cannot be removed."""
    module = load_module("build_product")
    monkeypatch.chdir(tmp_path)

    work_dir = tmp_path / ".macos-package"
    _make_component(work_dir, "tool", "1.0.0")

    output_pkg = tmp_path / "dist/tool-1.0.0.pkg"
    output_pkg.parent.mkdir(parents=True, exist_ok=True)
    output_pkg.write_text("old", encoding="utf-8")

    original_unlink = Path.unlink

    def _raise(self: Path, *args: object, **kwargs: object) -> None:
        if self == output_pkg:
            reason = PermissionError("denied")
            raise reason
        original_unlink(self, *args, **kwargs)

    class FakeCommand:
        def __init__(self) -> None:
            self.output: Path | None = None

        def __getitem__(self, args: tuple[str, ...]) -> FakeCommand:
            self.output = Path(args[-1])
            return self

        def __call__(self) -> None:
            if self.output is None:
                pytest.fail("productbuild invoked without an output path")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text("product", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(Path, "unlink", _raise)
    monkeypatch.setattr(module, "local", {"productbuild": fake})

    module.main(name="tool", version="1.0.0", include_license_panel=False)

    assert output_pkg.read_text(encoding="utf-8") == "product"
    assert "Could not remove existing output package" in capsys.readouterr().err
