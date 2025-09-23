"""Tests for the version resolution helper script."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from collections import abc as cabc
    from pathlib import Path

    import pytest
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)
    Path = typ.cast("type[object]", object)
    pytest = typ.cast("object", None)


def _read_key(path: Path, key: str) -> list[str]:
    """Return all values recorded for ``key`` within ``path``."""
    return [
        line.split("=", 1)[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{key}=")
    ]


def test_override_without_prefix_is_preserved(
    gh_output_files: tuple[Path, Path],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Respect manually supplied versions that lack the tag prefix."""
    env_file, output_file = gh_output_files
    module = load_module("compute_version")

    module.main(version="release-1")

    assert _read_key(env_file, "VERSION") == ["release-1"]
    assert _read_key(output_file, "version") == ["release-1"]


def test_override_with_prefix_is_trimmed(
    gh_output_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Strip the configured prefix from manual overrides."""
    env_file, output_file = gh_output_files
    module = load_module("compute_version")
    monkeypatch.setenv("TAG_VERSION_PREFIX", "v")

    module.main(version="v1.2.3")

    assert _read_key(env_file, "VERSION") == ["1.2.3"]
    assert _read_key(output_file, "version") == ["1.2.3"]


def test_tag_reference_with_custom_prefix(
    gh_output_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Extract the version from a Git tag using a custom prefix."""
    env_file, output_file = gh_output_files
    module = load_module("compute_version")
    monkeypatch.setenv("TAG_VERSION_PREFIX", "tool-")
    monkeypatch.setenv("GITHUB_REF", "refs/tags/tool-2.0.0")

    module.main()

    assert _read_key(env_file, "VERSION") == ["2.0.0"]
    assert _read_key(output_file, "version") == ["2.0.0"]


def test_fallback_to_build_metadata(
    gh_output_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
) -> None:
    """Populate build metadata when no tag or override is available."""
    env_file, output_file = gh_output_files
    module = load_module("compute_version")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "abcdef1234567890")

    module.main()

    assert _read_key(env_file, "VERSION") == ["0.0.0"]
    assert _read_key(env_file, "VERSION_BUILD_METADATA") == ["abcdef1"]
    assert _read_key(output_file, "version_build_metadata") == ["abcdef1"]


def test_cli_reads_input_environment(
    gh_output_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    load_module: cabc.Callable[[str], object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve inputs from ``INPUT_*`` environment variables via Cyclopts."""
    env_file, output_file = gh_output_files
    module = load_module("compute_version")
    monkeypatch.setenv("INPUT_VERSION", "v3.2.1")

    module.run_app(module.app)

    assert _read_key(env_file, "VERSION") == ["3.2.1"]
    assert _read_key(output_file, "version") == ["3.2.1"]
    assert "Resolved version: 3.2.1" in capsys.readouterr().out
