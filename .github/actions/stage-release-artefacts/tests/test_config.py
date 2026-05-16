"""Tests for stage-release-artefacts configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
prepend_to_syspath(SCRIPTS_DIR)

from stage_common import StageError
from stage_common.config import ArtefactConfig, StagingConfig, load_config


class TestArtefactConfig:
    """Tests for the ArtefactConfig dataclass."""

    def test_default_values(self) -> None:
        """ArtefactConfig has correct defaults."""
        config = ArtefactConfig(source="test.txt")
        assert config.source == "test.txt"
        assert config.required is True
        assert config.output is None
        assert config.destination is None
        assert config.alternatives == []


class TestStagingConfig:
    """Tests for the StagingConfig dataclass."""

    def test_staging_dir(self, tmp_path: Path) -> None:
        """staging_dir returns correct path."""
        config = StagingConfig(
            workspace=tmp_path,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
        )
        expected = tmp_path / "dist" / "myapp_linux_x86_64"
        assert config.staging_dir() == expected

    def test_as_template_context(self, tmp_path: Path) -> None:
        """as_template_context returns complete context."""
        config = StagingConfig(
            workspace=tmp_path,
            bin_name="myapp",
            dist_dir="dist",
            checksum_algorithm="sha256",
            artefacts=[],
            platform="linux",
            arch="x86_64",
            target="x86_64-unknown-linux-gnu",
            bin_ext=".exe",
            target_key="linux-x86_64",
        )
        context = config.as_template_context()
        assert context["bin_name"] == "myapp"
        assert context["platform"] == "linux"
        assert context["arch"] == "x86_64"
        assert context["bin_ext"] == ".exe"
        assert context["staging_dir_name"] == "myapp_linux_x86_64"


class TestLoadConfig:
    """Tests for the load_config function."""

    @staticmethod
    def _setup_config(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        toml_content: str,
    ) -> Path:
        """Write a config file and set GITHUB_WORKSPACE for load_config tests."""
        monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content, encoding="utf-8")
        return config_file

    def test_loads_valid_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config parses valid TOML configuration."""
        config_file = self._setup_config(
            tmp_path,
            monkeypatch,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        config = load_config(config_file, "linux-x86_64")

        assert config.bin_name == "myapp"
        assert config.platform == "linux"
        assert len(config.artefacts) == 1

    def test_loads_target_artefacts_with_dest_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Target artefacts support optional entries and the dest alias."""
        config_file = self._setup_config(
            tmp_path,
            monkeypatch,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[targets.windows]
platform = "windows"
arch = "x86_64"
target = "x86_64-pc-windows-msvc"

[[targets.windows.artefacts]]
source = "target/orthohelp/{target}/release/powershell/MyTool/MyTool.psm1"
dest = "MyTool/MyTool.psm1"
required = false
""",
        )

        config = load_config(config_file, "windows")

        assert len(config.artefacts) == 2
        assert config.artefacts[1].destination == "MyTool/MyTool.psm1"
        assert config.artefacts[1].required is False

    def test_rejects_dest_and_destination_together(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Artefact entries must not provide both destination spellings."""
        config_file = self._setup_config(
            tmp_path,
            monkeypatch,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"
dest = "short"
destination = "long"

[targets.linux]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        with pytest.raises(StageError, match="both 'destination' and 'dest'"):
            load_config(config_file, "linux")

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """load_config raises for missing configuration file."""
        missing = tmp_path / "missing.toml"
        with pytest.raises(FileNotFoundError):
            load_config(missing, "linux-x86_64")

    def test_raises_for_missing_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config raises for missing target section."""
        config_file = self._setup_config(
            tmp_path,
            monkeypatch,
            """
[common]
bin_name = "myapp"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        with pytest.raises(StageError, match="Missing configuration key"):
            load_config(config_file, "windows-x86_64")
