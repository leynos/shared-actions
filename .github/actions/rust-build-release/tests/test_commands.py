"""Regression tests for rust-build-release command construction."""

from __future__ import annotations

import importlib.util
import re
import stat
import typing as typ
import unittest.mock as mock
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from plumbum.commands.processes import ProcessExecutionError
from rust_build_release_test_helpers import assert_no_toolchain_override

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[4]

if typ.TYPE_CHECKING:
    from types import ModuleType

ALNUM_TEXT = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
)


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


def _make_cross_decision(
    main_module: ModuleType,
    cross_path: Path | str | None,
    *,
    cargo_toolchain_spec: str,
    requires_cross_container: bool = False,
    use_cross_local_backend: bool = False,
) -> object:
    return main_module._CrossDecision(
        cross_path=str(cross_path) if cross_path is not None else None,
        cross_version="0.2.5",
        use_cross=True,
        cargo_toolchain_spec=cargo_toolchain_spec,
        use_cross_local_backend=use_cross_local_backend,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker",
        requires_cross_container=requires_cross_container,
    )


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


def test_build_cross_command_invokes_toolchain_override_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cross command construction invokes the toolchain override guard."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    decision = _make_cross_decision(
        main_module,
        cross_path,
        cargo_toolchain_spec="+bogus-nightly",
    )
    guard = mock.MagicMock()
    monkeypatch.setattr(
        main_module, "_assert_cross_command_has_no_toolchain_override", guard
    )

    main_module._build_cross_command(
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
    )

    guard.assert_called_once()


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(target=ALNUM_TEXT, features=ALNUM_TEXT, manifest_stem=ALNUM_TEXT)
def test_build_cross_command_property_omits_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
    features: str,
    manifest_stem: str,
) -> None:
    """Generated cross commands never include +toolchain tokens after argv[0]."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    decision = _make_cross_decision(
        main_module,
        cross_path,
        cargo_toolchain_spec="+bogus-nightly",
    )

    cmd = main_module._build_cross_command(
        decision,
        target,
        tmp_path / f"{manifest_stem}.toml",
        features,
    )

    assert_no_toolchain_override(list(cmd.formulate()))


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(prefix=st.lists(ALNUM_TEXT), override=ALNUM_TEXT, suffix=st.lists(ALNUM_TEXT))
def test_cross_command_guard_rejects_any_generated_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    prefix: list[str],
    override: str,
    suffix: list[str],
) -> None:
    """The cross guard rejects every +token after the executable."""
    main_module = _load_main_module(monkeypatch)
    argv = ["cross", *prefix, f"+{override}", *suffix]

    with pytest.raises(
        ValueError,
        match=r"cross command must not include a \+<toolchain> override",
    ):
        main_module._assert_cross_command_has_no_toolchain_override(argv)


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(args=st.lists(ALNUM_TEXT))
def test_cross_command_guard_accepts_generated_argv_without_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    """The cross guard accepts generated argv values with no +token."""
    main_module = _load_main_module(monkeypatch)

    main_module._assert_cross_command_has_no_toolchain_override(["cross", *args])


def test_cross_debug_output_matches_expected_argv_pattern(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: list[str],
) -> None:
    """The cross debug line reports the materialized cross argv."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    decision = _make_cross_decision(
        main_module,
        cross_path,
        cargo_toolchain_spec="+bogus-nightly",
    )

    build_cmd = main_module._build_cross_command(
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
    )
    main_module.typer.echo(f"::debug:: cross argv: {build_cmd}")

    assert len(echo_recorder) == 1
    assert re.match(
        r"^::debug:: cross argv: cross build --manifest-path .+ "
        r"--release --target .+$",
        echo_recorder[0],
    )


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


def test_cross_container_error_with_other_retcode_reraises_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: list[str],
) -> None:
    """Non-container cross failures are re-raised unchanged."""
    main_module = _load_main_module(monkeypatch)
    decision = _make_cross_decision(
        main_module,
        "/usr/bin/cross",
        cargo_toolchain_spec="+bogus-nightly",
    )
    exc = ProcessExecutionError(["cross", "build"], 2, "", "")

    with pytest.raises(ProcessExecutionError) as raised:
        main_module._handle_cross_container_error(
            exc,
            decision,
            "aarch64-unknown-linux-gnu",
            tmp_path / "Cargo.toml",
            "",
        )

    assert raised.value is exc
    assert echo_recorder == []


def test_required_cross_container_error_exits_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: list[str],
) -> None:
    """Required container failures are emitted as errors and exit."""
    main_module = _load_main_module(monkeypatch)
    decision = _make_cross_decision(
        main_module,
        "/usr/bin/cross",
        cargo_toolchain_spec="+bogus-nightly",
        requires_cross_container=True,
        use_cross_local_backend=False,
    )
    exc = ProcessExecutionError(["cross", "build"], 125, "", "")

    with pytest.raises(main_module.typer.Exit):
        main_module._handle_cross_container_error(
            exc,
            decision,
            "aarch64-unknown-linux-gnu",
            tmp_path / "Cargo.toml",
            "",
        )

    assert echo_recorder == [
        "::error:: cross failed to start a container runtime for target "
        "'aarch64-unknown-linux-gnu' (engine=docker)"
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

    assert calls == [
        [
            "cargo",
            "build",
            "--manifest-path",
            str(tmp_path / "Cargo.toml"),
            "--release",
            "--target",
            "aarch64-unknown-linux-gnu",
        ]
    ]


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
