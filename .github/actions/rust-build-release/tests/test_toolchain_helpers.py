"""Tests for toolchain helper utilities."""

from __future__ import annotations

import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from types import ModuleType

    import pytest


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
NIGHTLY_CRANELIFT_PROJECT = FIXTURES_DIR / "nightly-cranelift-project"


def test_read_default_toolchain_uses_config(
    toolchain_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """read_default_toolchain reads the configured TOOLCHAIN_VERSION file."""
    custom_file = tmp_path / "TOOLCHAIN_VERSION"
    custom_file.write_text("2.0.0\n", encoding="utf-8")
    monkeypatch.setattr(toolchain_module, "TOOLCHAIN_VERSION_FILE", custom_file)
    assert toolchain_module.read_default_toolchain() == "2.0.0"


def test_toolchain_triple_parses_valid_triple(toolchain_module: ModuleType) -> None:
    """toolchain_triple returns the embedded target triple when present."""
    triple = toolchain_module.toolchain_triple("1.89.0-x86_64-unknown-linux-gnu")
    assert triple == "x86_64-unknown-linux-gnu"


def test_toolchain_triple_returns_none_for_short_spec(
    toolchain_module: ModuleType,
) -> None:
    """toolchain_triple returns None when no triple is embedded."""
    assert toolchain_module.toolchain_triple("stable") is None
    assert toolchain_module.toolchain_triple("1.89.0-x86_64") is None


def test_read_repo_toolchain_prefers_repo_declared_nightly(
    toolchain_module: ModuleType,
) -> None:
    """Repo toolchain files outrank the action fallback."""
    toolchain = toolchain_module.read_repo_toolchain(
        NIGHTLY_CRANELIFT_PROJECT,
        Path("Cargo.toml"),
    )
    assert toolchain == "nightly-2026-03-26"


def test_read_manifest_rust_version_reads_package_msrv(
    toolchain_module: ModuleType,
) -> None:
    """Manifest fallback reads the package rust-version field."""
    rust_version = toolchain_module.read_manifest_rust_version(
        NIGHTLY_CRANELIFT_PROJECT,
        Path("Cargo.toml"),
    )
    assert rust_version == "1.88"


def test_read_manifest_rust_version_reads_workspace_package_msrv(
    toolchain_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Manifest fallback reads the workspace.package rust-version field."""
    project_dir = tmp_path / "workspace-project"
    project_dir.mkdir()
    (project_dir / "Cargo.toml").write_text(
        "\n".join(
            [
                "[workspace]",
                "members = []",
                "[workspace.package]",
                'rust-version = "1.88"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    rust_version = toolchain_module.read_manifest_rust_version(
        project_dir,
        Path("Cargo.toml"),
    )

    assert rust_version == "1.88"


def test_read_repo_toolchain_ignores_parent_toolchains_outside_project_dir(
    toolchain_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Toolchain discovery stays bounded to the supplied project directory."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "rust-toolchain.toml").write_text(
        "[toolchain]\nchannel='nightly-2099-01-01'\n",
        encoding="utf-8",
    )
    project_dir = outer / "project"
    project_dir.mkdir()
    (project_dir / "Cargo.toml").write_text(
        "[package]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )

    toolchain = toolchain_module.read_repo_toolchain(project_dir, Path("Cargo.toml"))

    assert toolchain is None


def test_iter_toolchain_search_dirs_stops_at_boundary(
    toolchain_module: ModuleType,
    tmp_path: Path,
) -> None:
    """stop_at causes the iterator to halt at the given directory."""
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)

    dirs = list(
        toolchain_module._iter_toolchain_search_dirs(
            deep,
            stop_at=tmp_path / "a",
        )
    )

    assert dirs[-1] == (tmp_path / "a").resolve()
    assert tmp_path.resolve() not in dirs


def test_read_repo_toolchain_ignores_malformed_rust_toolchain_toml(
    toolchain_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Malformed ``rust-toolchain.toml`` files do not fall back to legacy parsing."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "Cargo.toml").write_text(
        "[package]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (project_dir / "rust-toolchain.toml").write_text(
        "nightly-2099-01-01\ninvalid = [\n",
        encoding="utf-8",
    )

    toolchain = toolchain_module.read_repo_toolchain(project_dir, Path("Cargo.toml"))

    assert toolchain is None


def test_resolve_requested_toolchain_precedence(
    toolchain_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Explicit input, repo toolchain, MSRV, then fallback are used in order."""
    manifest_dir = tmp_path / "project"
    manifest_dir.mkdir()
    manifest = manifest_dir / "Cargo.toml"
    manifest.write_text(
        "[package]\nname='demo'\nversion='0.1.0'\nedition='2024'\nrust-version='1.77'\n",
        encoding="utf-8",
    )

    explicit = toolchain_module.resolve_requested_toolchain(
        "nightly-2026-03-26",
        project_dir=manifest_dir,
        manifest_path=Path("Cargo.toml"),
        fallback_toolchain="1.89.0",
    )
    assert explicit == "nightly-2026-03-26"

    whitespace_explicit = toolchain_module.resolve_requested_toolchain(
        "   ",
        project_dir=manifest_dir,
        manifest_path=Path("Cargo.toml"),
        fallback_toolchain="1.89.0",
    )
    assert whitespace_explicit == "1.77"

    (manifest_dir / "rust-toolchain.toml").write_text(
        "[toolchain]\nchannel='nightly-2026-03-27'\n",
        encoding="utf-8",
    )
    repo_declared = toolchain_module.resolve_requested_toolchain(
        "",
        project_dir=manifest_dir,
        manifest_path=Path("Cargo.toml"),
        fallback_toolchain="1.89.0",
    )
    assert repo_declared == "nightly-2026-03-27"

    (manifest_dir / "rust-toolchain.toml").unlink()
    manifest_declared = toolchain_module.resolve_requested_toolchain(
        "",
        project_dir=manifest_dir,
        manifest_path=Path("Cargo.toml"),
        fallback_toolchain="1.89.0",
    )
    assert manifest_declared == "1.77"

    manifest.write_text(
        "[package]\nname='demo'\nversion='0.1.0'\nedition='2024'\n",
        encoding="utf-8",
    )
    fallback = toolchain_module.resolve_requested_toolchain(
        "",
        project_dir=manifest_dir,
        manifest_path=Path("Cargo.toml"),
        fallback_toolchain="1.89.0",
    )
    assert fallback == "1.89.0"
