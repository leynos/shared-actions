"""Tests for stage-release-artefacts configuration loading."""

from __future__ import annotations

import typing as typ

import pytest
from stage_common import StageError
from stage_common.config import (
    ArtefactConfig,
    BinstallConfig,
    StagingConfig,
    load_config,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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

    _MINIMAL_NO_ARTEFACTS_TOML = """
[common]
bin_name = "myapp"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
"""

    @staticmethod
    def _setup_config(
        tmp_path: Path,
        toml_content: str,
    ) -> Path:
        """Write a config file for load_config tests."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content, encoding="utf-8")
        return config_file

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        """load_config parses valid TOML configuration."""
        config_file = self._setup_config(
            tmp_path,
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

        config = load_config(config_file, "linux-x86_64", workspace=tmp_path)

        assert config.bin_name == "myapp"
        assert config.platform == "linux"
        assert len(config.artefacts) == 1

    def test_loads_target_artefacts_with_dest_alias(self, tmp_path: Path) -> None:
        """Target artefacts support optional entries and the dest alias."""
        config_file = self._setup_config(
            tmp_path,
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

        config = load_config(config_file, "windows", workspace=tmp_path)

        assert len(config.artefacts) == 2
        assert config.artefacts[1].destination == "MyTool/MyTool.psm1"
        assert config.artefacts[1].required is False

    def test_loads_binstall_config(self, tmp_path: Path) -> None:
        """load_config parses optional cargo-binstall settings."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[common.binstall]
enabled = true
manifest_path = "crates/myapp/Cargo.toml"
version = "1.2.3"
archive_name = "{package_name}-{version}-{target}.tar.gz"
binary_source = "target/{target}/release/{bin_name}{bin_ext}"
binary_name = "{bin_name}{bin_ext}"
output = "binstall_archive_path"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"

[targets.linux-x86_64.binstall]
package_name = "target-package"
""",
        )

        config = load_config(config_file, "linux-x86_64", workspace=tmp_path)

        assert config.binstall.enabled is True
        assert config.binstall.manifest_path == "crates/myapp/Cargo.toml"
        assert config.binstall.version == "1.2.3"
        assert config.binstall.archive_name == (
            "{package_name}-{version}-{target}.tar.gz"
        )
        assert config.binstall.package_name == "target-package"

    def test_loads_binstall_only_config(self, tmp_path: Path) -> None:
        """Enabled cargo-binstall config does not require normal artefacts."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]
bin_name = "myapp"

[common.binstall]
enabled = true

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        config = load_config(config_file, "linux-x86_64", workspace=tmp_path)

        assert config.artefacts == []
        assert config.binstall.enabled is True

    def test_binstall_bin_name_substitutes_for_common(self, tmp_path: Path) -> None:
        """Binstall-only configs may set bin_name under [common.binstall]."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]

[common.binstall]
enabled = true
bin_name = "mytool"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        config = load_config(config_file, "linux-x86_64", workspace=tmp_path)

        assert config.bin_name == "mytool"

    def test_missing_bin_name_raises(self, tmp_path: Path) -> None:
        """Configs that omit bin_name everywhere fail with a clear message."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]

[common.binstall]
enabled = true

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )

        with pytest.raises(StageError, match="bin_name"):
            load_config(config_file, "linux-x86_64", workspace=tmp_path)

    def test_target_binstall_config_can_disable_common_default(
        self, tmp_path: Path
    ) -> None:
        """Target-level binstall config overrides common defaults."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[common.binstall]
enabled = true

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"

[targets.linux-x86_64.binstall]
enabled = false
""",
        )

        config = load_config(config_file, "linux-x86_64", workspace=tmp_path)

        assert config.binstall == BinstallConfig(enabled=False)

    def test_rejects_dest_and_destination_together(self, tmp_path: Path) -> None:
        """Artefact entries must not provide both destination spellings."""
        config_file = self._setup_config(
            tmp_path,
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
            load_config(config_file, "linux", workspace=tmp_path)

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """load_config raises for missing configuration file."""
        missing = tmp_path / "missing.toml"
        with pytest.raises(FileNotFoundError):
            load_config(missing, "linux-x86_64", workspace=tmp_path)

    def test_requires_workspace_parameter(self, tmp_path: Path) -> None:
        """load_config uses the caller-supplied workspace."""
        config_file = self._setup_config(
            tmp_path,
            """
[common]
bin_name = "myapp"

[[common.artefacts]]
source = "binary"

[targets.linux]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"
""",
        )
        workspace = tmp_path / "explicit-workspace"

        config = load_config(config_file, "linux", workspace=workspace)

        assert config.workspace == workspace

    @pytest.mark.parametrize(
        ("target_key", "expected_error"),
        [
            ("linux-x86_64", "No artefacts configured to stage"),
            ("windows-x86_64", "Missing configuration key"),
        ],
        ids=("binstall-disabled", "missing-target"),
    )
    def test_raises_for_invalid_load(
        self, tmp_path: Path, target_key: str, expected_error: str
    ) -> None:
        """load_config raises StageError for a missing target or missing artefacts."""
        config_file = self._setup_config(tmp_path, self._MINIMAL_NO_ARTEFACTS_TOML)

        with pytest.raises(StageError, match=expected_error):
            load_config(config_file, target_key, workspace=tmp_path)
