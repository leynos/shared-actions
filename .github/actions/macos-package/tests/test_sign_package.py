"""Tests for the installer signing helper."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_sign_package_creates_signed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gh_output_files: tuple[Path, Path],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Invoke ``productsign`` and emit a signed archive path."""
    _env_file, output_file = gh_output_files
    module = load_module("sign_package")
    monkeypatch.chdir(tmp_path)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    unsigned = dist_dir / "tool-1.0.0.pkg"
    unsigned.write_text("unsigned", encoding="utf-8")

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
                pytest.fail("productsign invoked without an output path")
            self.output.write_text("signed", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(module, "local", {"productsign": fake})

    module.main(name="tool", version="1.0.0", developer_id_installer="Developer ID")

    signed_pkg = dist_dir / "tool-1.0.0-signed.pkg"
    assert signed_pkg.read_text(encoding="utf-8") == "signed"
    assert fake.calls
    assert fake.calls[0][0] == "--sign"
    assert output_file.read_text(encoding="utf-8").strip().endswith(str(signed_pkg))


def test_sign_package_requires_unsigned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Raise an error when the unsigned package is missing."""
    module = load_module("sign_package")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(module.ActionError):
        module.main(name="tool", version="1.0.0", developer_id_installer="Developer ID")


def test_sign_package_warns_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gh_output_files: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Log a warning when the signed package cannot be removed."""
    _env_file, output_file = gh_output_files
    module = load_module("sign_package")
    monkeypatch.chdir(tmp_path)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    unsigned = dist_dir / "tool-1.0.0.pkg"
    unsigned.write_text("unsigned", encoding="utf-8")
    signed_pkg = dist_dir / "tool-1.0.0-signed.pkg"
    signed_pkg.write_text("old", encoding="utf-8")

    original_unlink = Path.unlink

    def _raise(self: Path, *args: object, **kwargs: object) -> None:
        if self == signed_pkg:
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
                pytest.fail("productsign invoked without an output path")
            self.output.write_text("signed", encoding="utf-8")

    fake = FakeCommand()
    monkeypatch.setattr(Path, "unlink", _raise)
    monkeypatch.setattr(module, "local", {"productsign": fake})

    module.main(name="tool", version="1.0.0", developer_id_installer="Developer ID")

    assert output_file.read_text(encoding="utf-8").strip().endswith(str(signed_pkg))
    assert "Could not remove existing signed package" in capsys.readouterr().err
