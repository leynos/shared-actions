"""Tests for PowerShell sidecar staging and module-name validation."""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import given
from stage_common import StageError
from stage_common.config import ArtefactConfig
from stage_common.pipeline import stage_artefacts

from conftest import (
    HYPOTHESIS_SETTINGS,
    TEMPLATE_SAFE_PS_MODULE_NAMES,
    make_linux_config,
    make_powershell_workspace,
    make_windows_config,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestStageArtefactsPowerShell:
    """Tests for PowerShell sidecar staging and module-name validation."""

    def test_stages_windows_powershell_help_dir(self, tmp_path: Path) -> None:
        """Windows PowerShell sidecars are staged under the module directory."""
        workspace, _ = make_powershell_workspace(tmp_path)

        config = make_windows_config(workspace)

        result = stage_artefacts(config, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 4
        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert (result.staging_dir / "MyTool" / "MyTool.psd1").exists()
        assert (result.staging_dir / "MyTool" / "en-US" / "MyTool-help.xml").exists()
        assert (
            result.staging_dir / "MyTool" / "en-US" / "about_MyTool.help.txt"
        ).exists()
        assert result.powershell_help_dir == result.staging_dir / "MyTool"

    def test_linux_skips_absent_optional_powershell_help(self, tmp_path: Path) -> None:
        """Linux staging succeeds when optional PowerShell sidecars are absent."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        binary = workspace / "mytool"
        binary.write_text("binary content", encoding="utf-8")

        config = make_linux_config(
            workspace,
            [
                ArtefactConfig(source="mytool"),
                ArtefactConfig(
                    source=(
                        "target/orthohelp/{target}/release/powershell/"
                        "MyTool/MyTool.psm1"
                    ),
                    destination="MyTool/MyTool.psm1",
                    required=False,
                ),
            ],
        )

        result = stage_artefacts(config, ps_module_name="MyTool")

        assert len(result.staged_artefacts) == 1
        assert result.powershell_help_dir is None

    def test_missing_required_powershell_help_fails(self, tmp_path: Path) -> None:
        """Missing required PowerShell sidecars fail with the attempted path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source=(
                        "target/orthohelp/{target}/release/powershell/"
                        "MyTool/MyTool.psm1"
                    ),
                    destination="MyTool/MyTool.psm1",
                ),
            ],
        )

        with pytest.raises(StageError, match=r"MyTool/MyTool\.psm1"):
            stage_artefacts(config, ps_module_name="MyTool")

    def test_empty_ps_module_name_leaves_powershell_help_dir_empty(
        self, tmp_path: Path
    ) -> None:
        """The result stays empty unless the module name is provided."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_dir = workspace / "powershell" / "MyTool"
        source_dir.mkdir(parents=True)
        (source_dir / "MyTool.psm1").write_text("module", encoding="utf-8")

        config = make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source="powershell/MyTool/MyTool.psm1",
                    destination="MyTool/MyTool.psm1",
                ),
            ],
        )

        result = stage_artefacts(config)

        assert (result.staging_dir / "MyTool" / "MyTool.psm1").exists()
        assert result.powershell_help_dir is None

    @pytest.mark.parametrize(
        "ps_module_name",
        [
            ".",
            "..",
            "foo/bar",
            "foo\\bar",
        ],
    )
    def test_disallowed_ps_module_names_do_not_set_powershell_help_dir(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Invalid module names are ignored by the public staging API."""
        workspace, _ = make_powershell_workspace(tmp_path)
        config = make_windows_config(workspace)

        result = stage_artefacts(config, ps_module_name=ps_module_name)

        assert len(result.staged_artefacts) == 4
        assert result.powershell_help_dir is None

    @HYPOTHESIS_SETTINGS
    @given(ps_module_name=TEMPLATE_SAFE_PS_MODULE_NAMES)
    def test_valid_ps_module_names_resolve_when_staged(
        self, tmp_path: Path, ps_module_name: str
    ) -> None:
        """Single-segment module names resolve when files are staged under them."""
        workspace = tmp_path / "workspace"
        source_dir = workspace / "powershell" / ps_module_name
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "module.psm1").write_text("module", encoding="utf-8")
        config = make_windows_config(
            workspace,
            [
                ArtefactConfig(
                    source=f"powershell/{ps_module_name}/module.psm1",
                    destination=f"{ps_module_name}/module.psm1",
                ),
            ],
        )

        result = stage_artefacts(config, ps_module_name=ps_module_name)

        assert result.powershell_help_dir == result.staging_dir / ps_module_name
