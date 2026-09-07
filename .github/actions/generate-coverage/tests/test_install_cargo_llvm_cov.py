"""Verify how the cargo-llvm-cov installer resolves its manifest entry.

The installer resolves its entry from ``.github/tool-manifest.toml`` with the
``install-tool`` resolver, so this module holds the pinned version to the
manifest for every runner the resolver knows and covers the typed failures
that resolution reports: an unknown version, an unreadable manifest, a schema
this installer does not read, and a resolver that cannot be loaded.

Archive handling lives in ``test_install_cargo_llvm_cov_archives.py`` and the
entry point in ``test_install_cargo_llvm_cov_entrypoint.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import typer
from _coverage_test_support import _exit_code
from _llvm_cov_test_support import (
    INSTALLER_COPIES,
    RUNNERS,
    _job_environment,
)

if typ.TYPE_CHECKING:
    from types import ModuleType


def test_both_actions_ship_the_same_installer() -> None:
    """The two copies are byte-identical, so a fix in one cannot miss the other."""
    contents = {name: path.read_bytes() for name, path in INSTALLER_COPIES.items()}
    assert contents["generate-coverage"] == contents["ratchet-coverage"]


@pytest.mark.parametrize("runner", list(RUNNERS.values()), ids=list(RUNNERS))
def test_pinned_version_resolves_from_the_manifest_for_every_runner(
    install_llvm_cov_module: ModuleType, runner: tuple[str, str]
) -> None:
    """The version the script pins is in the manifest for each supported runner."""
    tool = install_llvm_cov_module.resolve_tool(runner=runner)

    version = install_llvm_cov_module.CARGO_LLVM_COV_VERSION
    assert f"/v{version}/" in tool.url, tool.url
    assert tool.expected_version == f"cargo-llvm-cov {version}"
    assert tool.version_args == ("llvm-cov", "--version")
    assert len(tool.sha256) == 64
    assert tool.binary.endswith(".exe") == (runner[0] == "Windows")


def test_manifest_pin_is_the_layout_aware_release(
    install_llvm_cov_module: ModuleType,
) -> None:
    """The pin is at least 0.9.0, the first release reading Cargo's new layout.

    cargo 1.100 nightlies place test executables under
    ``debug/build/<package>/<hash>/out`` and 0.6.24 searched ``debug/deps``,
    failing with "not found object files" after every test passed.
    """
    major, minor, _patch = (
        int(part) for part in install_llvm_cov_module.CARGO_LLVM_COV_VERSION.split(".")
    )
    assert (major, minor) >= (0, 9)


def test_unknown_version_is_refused_rather_than_floated(
    install_llvm_cov_module: ModuleType,
) -> None:
    """A version the manifest does not list raises a typed resolution error."""
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool("0.0.1", runner=RUNNERS["linux-x64"])

    assert excinfo.value.kind == "unknown-version"


def test_manifest_with_another_schema_is_refused(
    install_llvm_cov_module: ModuleType,
) -> None:
    """A manifest schema this installer does not read fails closed, by kind."""
    manifest = {"schema": 2, "tool": []}

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool(
            manifest=manifest, runner=RUNNERS["linux-x64"]
        )

    assert excinfo.value.kind == "unsupported-schema"


def test_unreadable_manifest_is_a_typed_error(
    install_llvm_cov_module: ModuleType, tmp_path: Path
) -> None:
    """A missing manifest is reported by kind, not as a stack trace."""
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_manifest(tmp_path / "absent.toml")

    assert excinfo.value.kind == install_llvm_cov_module.MANIFEST_UNREADABLE


def test_a_missing_resolver_is_a_typed_error_without_output(
    install_llvm_cov_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """An absent resolver raises the bounded kind and the query stays silent.

    Loading the resolver used to exit the process from inside the query, so
    a caller could not convert the failure into a metric.
    """
    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_resolver(tmp_path / "absent.py")

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_resolver_raising_on_import_is_a_typed_error(
    install_llvm_cov_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A resolver whose module body raises is reported by kind, not as a traceback."""
    resolver = tmp_path / "resolve_tool.py"
    resolver.write_text('raise RuntimeError("resolver is broken")\n', encoding="utf-8")

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.load_resolver(resolver)

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_resolve_tool_reports_a_failing_resolver_load_by_kind(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_tool`` surfaces a resolver-load failure as its own typed error."""
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )

    with pytest.raises(install_llvm_cov_module.ToolResolutionError) as excinfo:
        install_llvm_cov_module.resolve_tool(runner=RUNNERS["linux-x64"])

    assert excinfo.value.kind == install_llvm_cov_module.RESOLVER_UNAVAILABLE


def test_resolve_tool_uses_an_injected_resolver(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected resolver is used as given, so the query loads no file.

    ``RESOLVER_PATH`` points at nothing for the duration, which would fail the
    call were the dependency still resolved from disk.
    """
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )
    resolver = install_llvm_cov_module.load_resolver(
        Path(install_llvm_cov_module.__file__).resolve().parents[3]
        / "actions"
        / "install-tool"
        / "scripts"
        / "resolve_tool.py"
    )

    tool = install_llvm_cov_module.resolve_tool(
        runner=RUNNERS["linux-x64"], resolver=resolver
    )

    assert tool.expected_version == (
        f"cargo-llvm-cov {install_llvm_cov_module.CARGO_LLVM_COV_VERSION}"
    )


def test_main_reports_a_failing_resolver_load_as_a_metric(
    install_llvm_cov_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main`` converts a resolver-load failure into the bounded metric and exit 1."""
    _binary, _github_path, summary = _job_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        install_llvm_cov_module, "RESOLVER_PATH", tmp_path / "absent.py"
    )

    with pytest.raises(typer.Exit) as excinfo:
        install_llvm_cov_module.main()

    assert _exit_code(excinfo.value) == 1
    assert "metric cargo-llvm-cov.resolve=resolver-unavailable" in summary.read_text(
        encoding="utf-8"
    )
