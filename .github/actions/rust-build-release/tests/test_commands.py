"""Regression tests for rust-build-release command construction."""

from __future__ import annotations

import importlib.util
import stat
import typing as typ
from pathlib import Path

import pytest
from plumbum.commands.processes import ProcessExecutionError
from rust_build_release_test_helpers import assert_no_toolchain_override

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[4]

if typ.TYPE_CHECKING:
    from types import ModuleType


def _make_cross_executable(tmp_path: Path) -> Path:
    cross_path = tmp_path / "cross"
    cross_path.write_text("#!/bin/sh\n", encoding="utf-8")
    cross_path.chmod(
        cross_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return cross_path


def _load_main_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("GITHUB_ACTION_PATH", str(REPO_ROOT))
    monkeypatch.syspath_prepend(str(SRC_DIR))

    import packaging
    import packaging.version as packaging_version

    spec = importlib.util.spec_from_file_location(
        "rbr_main_commands", SRC_DIR / "main.py"
    )
    if spec is None or spec.loader is None:
        msg = f"failed to load main.py from {SRC_DIR}"
        raise RuntimeError(msg)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if getattr(packaging, "version", None) is packaging_version:
            delattr(packaging, "version")
    return module


def test_build_cross_command_never_injects_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cross commands start with `cross build` and omit +toolchain arguments."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    decision = main_module._CrossDecision(
        cross_path=str(cross_path),
        cross_version="0.2.5",
        use_cross=True,
        cargo_toolchain_spec="+bogus-nightly",
        use_cross_local_backend=False,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker",
        requires_cross_container=False,
    )

    cmd = main_module._build_cross_command(
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
    )

    parts = list(cmd.formulate())
    assert parts[:3] == ["cross", "build", "--manifest-path"]
    assert_no_toolchain_override(parts)


def test_cross_container_fallback_keeps_cargo_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The container-error fallback remains cargo-only and keeps +toolchain."""
    main_module = _load_main_module(monkeypatch)
    calls: list[list[str]] = []

    def fake_run_cmd(cmd: object) -> None:
        formulated = list(cmd.formulate())
        if formulated:
            formulated[0] = Path(formulated[0]).name
        calls.append(formulated)

    monkeypatch.setattr(main_module, "run_cmd", fake_run_cmd)
    decision = main_module._CrossDecision(
        cross_path="/usr/bin/cross",
        cross_version="0.2.5",
        use_cross=True,
        cargo_toolchain_spec="+bogus-nightly",
        use_cross_local_backend=False,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker",
        requires_cross_container=False,
    )
    exc = ProcessExecutionError(["cross", "build"], 125, "", "")

    main_module._handle_cross_container_error(
        exc,
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
    )

    assert calls == [
        [
            "cargo",
            "+bogus-nightly",
            "build",
            "--manifest-path",
            str(tmp_path / "Cargo.toml"),
            "--release",
            "--target",
            "aarch64-unknown-linux-gnu",
        ]
    ]


def test_cross_container_fallback_without_cargo_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The container-error fallback uses no +toolchain when none is configured."""
    main_module = _load_main_module(monkeypatch)
    calls: list[list[str]] = []

    def fake_run_cmd(cmd: object) -> None:
        formulated = list(cmd.formulate())
        if formulated:
            formulated[0] = Path(formulated[0]).name
        calls.append(formulated)

    monkeypatch.setattr(main_module, "run_cmd", fake_run_cmd)
    decision = main_module._CrossDecision(
        cross_path="/usr/bin/cross",
        cross_version="0.2.5",
        use_cross=True,
        cargo_toolchain_spec="",
        use_cross_local_backend=False,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker",
        requires_cross_container=False,
    )
    exc = ProcessExecutionError(["cross", "build"], 125, "", "")

    main_module._handle_cross_container_error(
        exc,
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
    )

    assert calls
    fallback_call = calls[0]
    assert fallback_call[0] == "cargo"
    assert all(
        not (isinstance(arg, str) and arg.startswith("+")) for arg in fallback_call[1:]
    )


def test_cross_command_guard_rejects_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross argv guard catches accidental +toolchain injection."""
    main_module = _load_main_module(monkeypatch)
    with pytest.raises(
        ValueError,
        match=r"cross command must not include a \+<toolchain> override",
    ):
        main_module._assert_cross_command_has_no_toolchain_override(
            ["cross", "build", "--manifest-path", "Cargo.toml", "+bogus-nightly"]
        )
