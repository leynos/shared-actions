"""Tests for :mod:`cargo_utils`."""

from __future__ import annotations

from pathlib import Path

import pytest

from cargo_utils import (
    ManifestError,
    find_workspace_root,
    get_bin_name,
    get_package_field,
    get_workspace_version,
    read_manifest,
    resolve_version,
)


class TestReadManifest:
    """Tests for :func:`read_manifest`."""

    def test_reads_valid_manifest(self, tmp_path: Path) -> None:
        """A valid Cargo.toml should be parsed into a dictionary."""
        manifest_path = tmp_path / "Cargo.toml"
        manifest_path.write_text(
            """\
[package]
name = "test-pkg"
version = "1.2.3"
"""
        )

        result = read_manifest(manifest_path)

        assert result["package"]["name"] == "test-pkg"
        assert result["package"]["version"] == "1.2.3"

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """A missing manifest should raise ManifestError."""
        missing_path = tmp_path / "Cargo.toml"

        with pytest.raises(ManifestError, match="Manifest not found"):
            read_manifest(missing_path)

    def test_raises_for_invalid_toml(self, tmp_path: Path) -> None:
        """Invalid TOML syntax should raise ManifestError."""
        manifest_path = tmp_path / "Cargo.toml"
        manifest_path.write_text("invalid [ toml ]]")

        with pytest.raises(ManifestError, match="Invalid TOML"):
            read_manifest(manifest_path)


class TestGetPackageField:
    """Tests for :func:`get_package_field`."""

    def test_extracts_name(self) -> None:
        """The name field should be extracted from [package]."""
        manifest = {"package": {"name": "example", "version": "1.0.0"}}

        assert get_package_field(manifest, "name", Path("Cargo.toml")) == "example"

    def test_extracts_version(self) -> None:
        """The version field should be extracted from [package]."""
        manifest = {"package": {"name": "example", "version": "2.3.4"}}

        assert get_package_field(manifest, "version", Path("Cargo.toml")) == "2.3.4"

    def test_strips_whitespace(self) -> None:
        """Field values should have leading and trailing whitespace removed."""
        manifest = {"package": {"name": "  padded  ", "version": " 1.0.0 "}}

        assert get_package_field(manifest, "name", Path("Cargo.toml")) == "padded"
        assert get_package_field(manifest, "version", Path("Cargo.toml")) == "1.0.0"

    def test_raises_for_missing_package_table(self) -> None:
        """A manifest without [package] should raise ManifestError."""
        manifest: dict[str, object] = {}

        with pytest.raises(ManifestError, match="missing \\[package\\] table"):
            get_package_field(manifest, "name", Path("Cargo.toml"))

    def test_raises_for_missing_field(self) -> None:
        """A missing field should raise ManifestError."""
        manifest = {"package": {"name": "example"}}

        with pytest.raises(ManifestError, match=r"package\.version is missing"):
            get_package_field(manifest, "version", Path("Cargo.toml"))

    def test_raises_for_empty_field(self) -> None:
        """An empty field value should raise ManifestError."""
        manifest = {"package": {"name": "", "version": "1.0.0"}}

        with pytest.raises(ManifestError, match=r"package\.name is missing or empty"):
            get_package_field(manifest, "name", Path("Cargo.toml"))


class TestGetBinName:
    """Tests for :func:`get_bin_name`."""

    def test_uses_bin_name_when_present(self) -> None:
        """The first [[bin]].name should take precedence."""
        manifest = {
            "package": {"name": "my-lib", "version": "1.0.0"},
            "bin": [{"name": "my-cli", "path": "src/main.rs"}],
        }

        assert get_bin_name(manifest, Path("Cargo.toml")) == "my-cli"

    def test_falls_back_to_package_name(self) -> None:
        """Without [[bin]], the package name should be used."""
        manifest = {"package": {"name": "my-package", "version": "1.0.0"}}

        assert get_bin_name(manifest, Path("Cargo.toml")) == "my-package"

    def test_ignores_empty_bin_list(self) -> None:
        """An empty [[bin]] list should fall back to package name."""
        manifest = {
            "package": {"name": "fallback", "version": "1.0.0"},
            "bin": [],
        }

        assert get_bin_name(manifest, Path("Cargo.toml")) == "fallback"

    def test_ignores_bin_without_name(self) -> None:
        """A [[bin]] entry without a name should fall back to package name."""
        manifest = {
            "package": {"name": "fallback", "version": "1.0.0"},
            "bin": [{"path": "src/main.rs"}],
        }

        assert get_bin_name(manifest, Path("Cargo.toml")) == "fallback"

    def test_strips_bin_name_whitespace(self) -> None:
        """The bin name should have whitespace stripped."""
        manifest = {
            "package": {"name": "pkg", "version": "1.0.0"},
            "bin": [{"name": "  spaced  ", "path": "src/main.rs"}],
        }

        assert get_bin_name(manifest, Path("Cargo.toml")) == "spaced"


class TestFindWorkspaceRoot:
    """Tests for :func:`find_workspace_root`."""

    def test_finds_workspace_in_parent(self, tmp_path: Path) -> None:
        """A workspace manifest in a parent directory should be found."""
        workspace_manifest = tmp_path / "Cargo.toml"
        workspace_manifest.write_text(
            """\
[workspace]
members = ["member"]

[workspace.package]
version = "1.0.0"
"""
        )

        member_dir = tmp_path / "member"
        member_dir.mkdir()
        member_manifest = member_dir / "Cargo.toml"
        member_manifest.write_text(
            """\
[package]
name = "member"
version.workspace = true
"""
        )

        result = find_workspace_root(member_dir)

        assert result == workspace_manifest

    def test_returns_none_when_no_workspace(self, tmp_path: Path) -> None:
        """When no workspace exists, None should be returned."""
        package_manifest = tmp_path / "Cargo.toml"
        package_manifest.write_text(
            """\
[package]
name = "standalone"
version = "1.0.0"
"""
        )

        result = find_workspace_root(tmp_path)

        assert result is None

    def test_returns_manifest_at_start_dir(self, tmp_path: Path) -> None:
        """A workspace manifest at the start directory should be found."""
        workspace_manifest = tmp_path / "Cargo.toml"
        workspace_manifest.write_text(
            """\
[workspace]
members = []
"""
        )

        result = find_workspace_root(tmp_path)

        assert result == workspace_manifest


class TestGetWorkspaceVersion:
    """Tests for :func:`get_workspace_version`."""

    @pytest.mark.parametrize(
        ("manifest_content", "expected"),
        [
            pytest.param(
                "[workspace]\n"
                'members = ["member"]\n\n'
                "[workspace.package]\n"
                'version = "2.0.0"\n',
                "2.0.0",
                id="reads_workspace_version",
            ),
            pytest.param(
                '[package]\nname = "no-workspace"\nversion = "1.0.0"\n',
                None,
                id="returns_none_for_missing_workspace",
            ),
            pytest.param(
                '[workspace]\nmembers = ["member"]\n',
                None,
                id="returns_none_for_missing_version",
            ),
            pytest.param(
                "[workspace]\n"
                "members = []\n\n"
                "[workspace.package]\n"
                'version = "  3.0.0  "\n',
                "3.0.0",
                id="strips_version_whitespace",
            ),
        ],
    )
    def test_workspace_version_extraction(
        self, tmp_path: Path, manifest_content: str, expected: str | None
    ) -> None:
        """Verify workspace version extraction handles various manifest formats."""
        manifest_path = tmp_path / "Cargo.toml"
        manifest_path.write_text(manifest_content)

        result = get_workspace_version(manifest_path)

        assert result == expected


class TestResolveVersion:
    """Tests for :func:`resolve_version`."""

    def test_returns_direct_version(self, tmp_path: Path) -> None:
        """A direct version string should be returned as-is."""
        manifest = {"package": {"name": "pkg", "version": "1.2.3"}}
        manifest_path = tmp_path / "Cargo.toml"

        result = resolve_version(manifest, manifest_path)

        assert result == "1.2.3"

    def test_resolves_workspace_inherited_version(self, tmp_path: Path) -> None:
        """A workspace-inherited version should be resolved from the root."""
        workspace_manifest = tmp_path / "Cargo.toml"
        workspace_manifest.write_text(
            """\
[workspace]
members = ["member"]

[workspace.package]
version = "2.0.0"
"""
        )

        member_dir = tmp_path / "member"
        member_dir.mkdir()
        member_manifest_path = member_dir / "Cargo.toml"

        manifest = {"package": {"name": "member", "version": {"workspace": True}}}

        result = resolve_version(manifest, member_manifest_path)

        assert result == "2.0.0"

    def test_raises_when_workspace_not_found(self, tmp_path: Path) -> None:
        """Missing workspace root should raise ManifestError."""
        manifest = {"package": {"name": "orphan", "version": {"workspace": True}}}
        manifest_path = tmp_path / "Cargo.toml"

        with pytest.raises(ManifestError, match="workspace root"):
            resolve_version(manifest, manifest_path)

    def test_raises_when_workspace_version_missing(self, tmp_path: Path) -> None:
        """Workspace without version should raise ManifestError."""
        workspace_manifest = tmp_path / "Cargo.toml"
        workspace_manifest.write_text(
            """\
[workspace]
members = ["member"]
"""
        )

        member_dir = tmp_path / "member"
        member_dir.mkdir()
        member_manifest_path = member_dir / "Cargo.toml"

        manifest = {"package": {"name": "member", "version": {"workspace": True}}}

        with pytest.raises(ManifestError, match=r"workspace\.package"):
            resolve_version(manifest, member_manifest_path)

    def test_raises_for_missing_package_table(self, tmp_path: Path) -> None:
        """A manifest without [package] should raise ManifestError."""
        manifest: dict[str, object] = {}
        manifest_path = tmp_path / "Cargo.toml"

        with pytest.raises(ManifestError, match="missing \\[package\\] table"):
            resolve_version(manifest, manifest_path)

    def test_raises_for_empty_version(self, tmp_path: Path) -> None:
        """An empty version string should raise ManifestError."""
        manifest = {"package": {"name": "pkg", "version": ""}}
        manifest_path = tmp_path / "Cargo.toml"

        with pytest.raises(ManifestError, match=r"package\.version is missing"):
            resolve_version(manifest, manifest_path)
