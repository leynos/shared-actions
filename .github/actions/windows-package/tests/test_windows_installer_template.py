"""Tests for the Windows installer templating helpers."""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1] / "scripts"
WINDOWS_INSTALLER_PATH = MODULE_DIR / "windows_installer" / "__init__.py"
GENERATE_WXS_PATH = MODULE_DIR / "generate_wxs.py"


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive guard
        msg = f"cannot load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, types.ModuleType)
    return module


WINDOWS_INSTALLER = _load_module(WINDOWS_INSTALLER_PATH, "windows_installer")
GENERATE_WXS = _load_module(GENERATE_WXS_PATH, "generate_wxs")


def test_render_default_wxs_builds_directory_structure(tmp_path: Path) -> None:
    """Render a WiX authoring file with nested directories and license variable."""
    app_path = tmp_path / "build" / "app.exe"
    app_path.parent.mkdir()
    app_path.write_bytes(b"binary")

    doc_path = tmp_path / "docs" / "guide.txt"
    doc_path.parent.mkdir()
    doc_path.write_text("manual", encoding="utf-8")

    license_path = tmp_path / "LICENSE.rtf"
    license_path.write_text("{\\rtf1}", encoding="utf-8")

    application_spec = WINDOWS_INSTALLER.parse_file_specification(str(app_path))
    doc_spec = WINDOWS_INSTALLER.parse_file_specification(f"{doc_path}|docs/guide.txt")

    options = WINDOWS_INSTALLER.prepare_template_options(
        version="1.2.3",
        architecture="arm64",
        application=application_spec,
        product_name="Sample App",
        manufacturer="Shared Actions",
        install_dir_name="SampleApp",
        description="Sample App Installer",
        upgrade_code="12345678-1234-1234-1234-1234567890ab",
        additional_files=[doc_spec],
        license_path=str(license_path),
    )

    authoring = WINDOWS_INSTALLER.render_default_wxs(options)

    assert 'Name="Sample App"' in authoring
    assert 'Manufacturer="Shared Actions"' in authoring
    assert 'Version="1.2.3"' in authoring
    assert 'UpgradeCode="12345678-1234-1234-1234-1234567890AB"' in authoring
    assert '<StandardDirectory Id="ProgramFiles64Folder"' in authoring
    assert '<Directory Id="INSTALLFOLDER" Name="SampleApp">' in authoring
    # File sources should be converted to Windows-style absolute paths
    assert 'app.exe" />' in authoring
    assert 'docs\\guide.txt"' in authoring
    assert f'Value="{str(license_path).replace("/", "\\")}"' in authoring


def test_generate_wxs_cli_writes_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI helper should emit WiX authoring and print the destination path."""
    app_path = tmp_path / "app.exe"
    app_path.write_bytes(b"binary")

    extra_path = tmp_path / "assets" / "logo.ico"
    extra_path.parent.mkdir()
    extra_path.write_bytes(b"ico")

    output_path = tmp_path / "installer" / "Package.wxs"

    GENERATE_WXS.main(  # type: ignore[attr-defined]
        output=output_path,
        version="0.9.0",
        architecture="x64",
        application=str(app_path),
        product_name="Widget",
        manufacturer="Shared Actions",
        additional_file=[f"{extra_path}|assets/logo.ico"],
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == str(output_path)
    assert output_path.exists()
    contents = output_path.read_text(encoding="utf-8")
    assert "Widget" in contents
    assert "ProgramFiles64Folder" in contents
    assert "logo.ico" in contents


def test_parse_file_specification_rejects_empty() -> None:
    """Empty file specifications should raise a TemplateError."""
    with pytest.raises(WINDOWS_INSTALLER.TemplateError):
        WINDOWS_INSTALLER.parse_file_specification("")


def test_prepare_template_options_unknown_architecture(tmp_path: Path) -> None:
    """Unsupported architectures should raise a TemplateError."""
    app_path = tmp_path / "bin" / "tool.exe"
    app_path.parent.mkdir()
    app_path.write_bytes(b"binary")

    spec = WINDOWS_INSTALLER.parse_file_specification(str(app_path))

    with pytest.raises(WINDOWS_INSTALLER.TemplateError):
        WINDOWS_INSTALLER.prepare_template_options(
            version="1.0.0",
            architecture="sparc",
            application=spec,
        )
