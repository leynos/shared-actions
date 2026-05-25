"""Tests for stage-release-artefacts command-line entry point."""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from syrupy.assertion import SnapshotAssertion

from stage import _emit_skipped_artefact_warnings, main
from stage_common import StageResult


def _redact_paths(text: str, *paths: Path) -> str:
    """Replace volatile absolute paths with stable tokens."""
    for i, path in enumerate(paths):
        text = text.replace(path.as_posix(), f"<DIR_{i}>")
    return text


class TestStageCli:
    """Tests for the stage.py main function."""

    @staticmethod
    def _write_config(tmp_path: Path, toml_content: str) -> Path:
        """Write a staging config file for CLI tests."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content, encoding="utf-8")
        return config_file

    def test_main_writes_empty_powershell_help_dir_for_absent_sidecar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SnapshotAssertion,
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

        output = output_file.read_text(encoding="utf-8")
        staging_dir = workspace / "dist" / "mytool_linux_x86_64"
        assert _redact_paths(output, staging_dir.parent, staging_dir) == snapshot(
            name="empty_powershell_help_dir"
        )

    def test_main_writes_powershell_help_dir_when_module_is_staged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SnapshotAssertion,
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
        staging_dir = workspace / "dist" / "mytool_windows_x86_64"
        assert _redact_paths(output, staging_dir.parent, staging_dir) == snapshot(
            name="populated_powershell_help_dir"
        )

    def test_main_writes_binstall_archive_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        snapshot: SnapshotAssertion,
    ) -> None:
        """CLI output includes the cargo-binstall archive path when enabled."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "1.2.3"\n',
            encoding="utf-8",
        )
        release_dir = workspace / "target/x86_64-unknown-linux-gnu/release"
        release_dir.mkdir(parents=True)
        (release_dir / "myapp").write_text("binary content", encoding="utf-8")
        output_file = tmp_path / "github-output"
        monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        config_file = self._write_config(
            tmp_path,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "target/{target}/release/{bin_name}"

[common.binstall]
enabled = true

[targets.linux]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        main(str(config_file), "linux")

        output = output_file.read_text(encoding="utf-8")
        staging_dir = workspace / "dist" / "myapp_linux_x86_64"
        normalized = re.sub(
            r'"[0-9a-f]{64}"',
            '"<sha256>"',
            _redact_paths(output, staging_dir.parent, staging_dir),
        )
        assert normalized == snapshot(name="binstall_archive_output")

    def test_emit_skipped_artefact_warnings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Skipped optional artefacts are emitted as GitHub Actions warnings."""
        result = StageResult(
            staging_dir=tmp_path / "dist" / "mytool_linux_x86_64",
            staged_artefacts=[],
            outputs={},
            checksums={},
            skipped_artefacts=["optional", "powershell/MyTool/MyTool.psm1"],
        )

        _emit_skipped_artefact_warnings(result)

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "::warning title=Artefact Skipped::Optional artefact missing: optional\n"
            "::warning title=Artefact Skipped::Optional artefact missing: "
            "powershell/MyTool/MyTool.psm1\n"
        )
