"""Tests for stage-release-artefacts command-line entry point."""

from __future__ import annotations

import typing as typ
from pathlib import Path

from syspath_hack import prepend_to_syspath

if typ.TYPE_CHECKING:
    import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage import main


class TestStageCli:
    """Tests for the stage.py main function."""

    @staticmethod
    def _write_config(tmp_path: Path, toml_content: str) -> Path:
        """Write a staging config file for CLI tests."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content, encoding="utf-8")
        return config_file

    def test_main_writes_empty_powershell_help_dir_for_absent_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI output keeps powershell_help_dir empty when no module is staged."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "mytool").write_text("binary", encoding="utf-8")
        output_file = tmp_path / "github-output"
        monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        config_file = self._write_config(
            tmp_path,
            """
[common]
bin_name = "mytool"

[[common.artefacts]]
source = "mytool"

[targets.linux]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        main(str(config_file), "linux", ps_module_name="MyTool")

        assert "powershell_help_dir=\n" in output_file.read_text(encoding="utf-8")

    def test_main_writes_powershell_help_dir_when_module_is_staged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI output includes powershell_help_dir when ps_module_name matches."""
        workspace = tmp_path / "workspace"
        module_dir = workspace / "powershell" / "MyTool"
        module_dir.mkdir(parents=True)
        (module_dir / "MyTool.psm1").write_text("module", encoding="utf-8")
        output_file = tmp_path / "github-output"
        monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        config_file = self._write_config(
            tmp_path,
            """
[common]
bin_name = "mytool"

[targets.windows]
platform = "windows"
arch = "x86_64"
target = "x86_64-pc-windows-msvc"

[[targets.windows.artefacts]]
source = "powershell/MyTool/MyTool.psm1"
destination = "MyTool/MyTool.psm1"
""",
        )

        main(str(config_file), "windows", ps_module_name="MyTool")

        output = output_file.read_text(encoding="utf-8")
        expected = workspace / "dist" / "mytool_windows_x86_64" / "MyTool"
        assert f"powershell_help_dir={expected.as_posix()}\n" in output
