"""Regression tests for rust-build-release command construction."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import stat
import sys
import typing as typ
from pathlib import Path, PurePosixPath

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


@dataclasses.dataclass(frozen=True)
class CrossDecisionConfig:
    """Overridable fields for constructing a _CrossDecision in tests."""

    cargo_toolchain_spec: str
    requires_cross_container: bool = False
    use_cross_local_backend: bool = False


def _make_cross_executable(tmp_path: Path) -> Path:
    """Create a minimal executable cross stub at *tmp_path*."""
    cross_path = tmp_path / "cross"
    cross_path.write_text("#!/bin/sh\n", encoding="utf-8")
    cross_path.chmod(
        cross_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return cross_path


def _load_main_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load main.py as a fresh module after patching the action path."""
    monkeypatch.setenv("GITHUB_ACTION_PATH", str(REPO_ROOT))
    monkeypatch.syspath_prepend(str(SRC_DIR))
    sys.modules.pop("gha", None)

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
    config: CrossDecisionConfig,
) -> object:
    """Construct a _CrossDecision from the given module, path, and config."""
    return main_module._CrossDecision(
        cross_path=str(cross_path) if cross_path is not None else None,
        cross_version="0.2.5",
        use_cross=True,
        cargo_toolchain_spec=config.cargo_toolchain_spec,
        use_cross_local_backend=config.use_cross_local_backend,
        docker_present=True,
        podman_present=False,
        has_container=True,
        container_engine="docker",
        requires_cross_container=config.requires_cross_container,
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


def test_build_cross_command_rejects_injected_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_build_cross_command raises ValueError when argv would contain +toolchain."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)

    # Patch the guard to inject a +toolchain token after the fact
    original_guard = main_module._assert_cross_command_has_no_toolchain_override

    def injecting_guard(cmd: object) -> None:
        # Simulate the bug: a +toolchain has been inserted into the argv
        original_guard(["cross", "+injected-nightly", "build"])

    monkeypatch.setattr(
        main_module, "_assert_cross_command_has_no_toolchain_override", injecting_guard
    )
    decision = _make_cross_decision(
        main_module,
        cross_path,
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
    )

    with pytest.raises(ValueError, match=r"\+<toolchain>"):
        main_module._build_cross_command(
            decision,
            "aarch64-unknown-linux-gnu",
            tmp_path / "Cargo.toml",
            "",
        )


@pytest.fixture
def cross_module_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[ModuleType, Path]:
    """Load the main module and create a stub cross executable."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    return main_module, cross_path


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(target=ALNUM_TEXT, features=ALNUM_TEXT, manifest_stem=ALNUM_TEXT)
def test_build_cross_command_property_omits_toolchain_override(
    cross_module_context: tuple[ModuleType, Path],
    target: str,
    features: str,
    manifest_stem: str,
) -> None:
    """Generated cross commands never include +toolchain tokens after argv[0]."""
    main_module, cross_path = cross_module_context
    decision = _make_cross_decision(
        main_module,
        cross_path,
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
    )

    cmd = main_module._build_cross_command(
        decision,
        target,
        Path(f"{manifest_stem}.toml"),
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


def test_cross_debug_output_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: list[str],
    snapshot: object,
) -> None:
    """The ::debug:: cross argv line matches the recorded snapshot."""
    main_module = _load_main_module(monkeypatch)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    cross_path = _make_cross_executable(tmp_path)
    # Use a stable manifest path suffix for the snapshot
    stable_manifest = PurePosixPath("/snapshot/Cargo.toml")
    decision = _make_cross_decision(
        main_module,
        cross_path,
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
    )
    monkeypatch.setattr(
        main_module,
        "resolve_requested_toolchain",
        lambda *_args, **_kw: "bogus-nightly",
    )
    monkeypatch.setattr(main_module, "_ensure_rustup_exec", lambda: "rustup")
    monkeypatch.setattr(
        main_module, "_resolve_toolchain", lambda *_args: "bogus-nightly"
    )
    monkeypatch.setattr(main_module, "_ensure_target_installed", lambda *_args: True)
    monkeypatch.setattr(main_module, "configure_windows_linkers", lambda *_args: None)
    monkeypatch.setattr(main_module, "_decide_cross_usage", lambda *_args: decision)
    monkeypatch.setattr(
        main_module, "_validate_cross_requirements", lambda *_args: None
    )
    monkeypatch.setattr(main_module, "_announce_build_mode", lambda *_args: None)
    monkeypatch.setattr(main_module, "run_cmd", lambda *_args: None)
    monkeypatch.setattr(
        main_module, "_manifest_argument", lambda _path: stable_manifest
    )

    main_module.main("aarch64-unknown-linux-gnu", "")

    debug_lines = [ln for ln in echo_recorder if "cross argv" in ln]
    assert len(debug_lines) == 1
    assert debug_lines[0] == snapshot


def test_assemble_build_command_sets_rustup_toolchain_env_when_explicit_toolchain_given(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When an explicit toolchain is supplied, RUSTUP_TOOLCHAIN is injected."""
    main_module = _load_main_module(monkeypatch)
    cross_path = _make_cross_executable(tmp_path)
    decision = _make_cross_decision(
        main_module,
        cross_path,
        CrossDecisionConfig(cargo_toolchain_spec=""),
    )

    cmd = main_module._assemble_build_command(
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
        "nightly",  # explicit_toolchain - non-empty triggers with_env
        "nightly",  # toolchain_name
    )

    # The returned wrapper must carry the RUSTUP_TOOLCHAIN binding.
    env = getattr(cmd, "env", None) or getattr(cmd._command, "env", {}) or {}
    assert env.get("RUSTUP_TOOLCHAIN") == "nightly", (
        f"Expected RUSTUP_TOOLCHAIN=nightly in env, got: {env}"
    )


@pytest.mark.parametrize(
    ("target_installed", "expect_exit"),
    [
        pytest.param(False, True, id="exits_when_not_installed_and_no_cross"),
        pytest.param(True, False, id="passes_when_target_installed"),
    ],
)
def test_check_target_support(
    monkeypatch: pytest.MonkeyPatch,
    target_installed: bool,  # noqa: FBT001
    expect_exit: bool,  # noqa: FBT001
) -> None:
    """_check_target_support exits only when target is missing and cross is disabled."""
    main_module = _load_main_module(monkeypatch)
    decision = main_module._CrossDecision(
        cross_path=None,
        cross_version=None,
        use_cross=False,
        cargo_toolchain_spec="+bogus-nightly",
        use_cross_local_backend=False,
        docker_present=False,
        podman_present=False,
        has_container=False,
        container_engine=None,
        requires_cross_container=False,
    )

    ctx = (
        pytest.raises(main_module.typer.Exit)
        if expect_exit
        else contextlib.nullcontext()
    )
    with ctx:
        main_module._check_target_support(
            decision,
            "bogus-nightly",
            "aarch64-unknown-linux-gnu",
            target_installed=target_installed,
        )


def test_assemble_build_command_returns_cargo_when_use_cross_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Build command assembly returns cargo when cross is disabled."""
    main_module = _load_main_module(monkeypatch)
    decision = main_module._CrossDecision(
        cross_path=None,
        cross_version=None,
        use_cross=False,
        cargo_toolchain_spec="+bogus-nightly",
        use_cross_local_backend=False,
        docker_present=False,
        podman_present=False,
        has_container=False,
        container_engine=None,
        requires_cross_container=False,
    )

    cmd = main_module._assemble_build_command(
        decision,
        "aarch64-unknown-linux-gnu",
        tmp_path / "Cargo.toml",
        "",
        "",
        "bogus-nightly",
    )

    parts = list(cmd.formulate())
    assert parts[0] == "cargo"


def test_assemble_build_command_raises_exit_on_toolchain_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Build command assembly exits when cross validation fails."""
    main_module = _load_main_module(monkeypatch)
    decision = _make_cross_decision(
        main_module,
        "/usr/bin/cross",
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
    )

    def fail_build_cross_command(*_args: object) -> object:
        msg = "cross command must not include a +<toolchain> override"
        raise ValueError(msg)

    monkeypatch.setattr(main_module, "_build_cross_command", fail_build_cross_command)

    with pytest.raises(main_module.typer.Exit):
        main_module._assemble_build_command(
            decision,
            "aarch64-unknown-linux-gnu",
            tmp_path / "Cargo.toml",
            "",
            "",
            "bogus-nightly",
        )


def test_main_emits_error_annotation_when_cross_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    echo_recorder: list[str],
) -> None:
    """main() emits ::error:: and exits 1 when the cross guard raises ValueError."""
    main_module = _load_main_module(monkeypatch)
    manifest_path = tmp_path / "Cargo.toml"
    manifest_path.write_text(
        "[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    cross_path = _make_cross_executable(tmp_path)
    decision = _make_cross_decision(
        main_module,
        cross_path,
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
    )

    def bad_build_cross(*_args: object) -> object:
        msg = (
            "cross command must not include a +<toolchain> override; "
            "found: ['+bogus-nightly']"
        )
        raise ValueError(msg)

    monkeypatch.setattr(
        main_module,
        "resolve_requested_toolchain",
        lambda *_args, **_kw: "bogus-nightly",
    )
    monkeypatch.setattr(main_module, "_ensure_rustup_exec", lambda: "rustup")
    monkeypatch.setattr(
        main_module, "_resolve_toolchain", lambda *_args: "bogus-nightly"
    )
    monkeypatch.setattr(main_module, "_ensure_target_installed", lambda *_args: True)
    monkeypatch.setattr(main_module, "configure_windows_linkers", lambda *_args: None)
    monkeypatch.setattr(main_module, "_decide_cross_usage", lambda *_args: decision)
    monkeypatch.setattr(
        main_module, "_validate_cross_requirements", lambda *_args: None
    )
    monkeypatch.setattr(main_module, "_announce_build_mode", lambda *_args: None)
    monkeypatch.setattr(main_module, "run_cmd", lambda *_args: None)
    monkeypatch.setattr(main_module, "_build_cross_command", bad_build_cross)

    with pytest.raises(main_module.typer.Exit) as exc_info:
        main_module.main("aarch64-unknown-linux-gnu", "")

    assert exc_info.value.exit_code == 1
    error_lines = [
        line for line in echo_recorder if "cross command validation failed" in line
    ]
    assert len(error_lines) == 1
    assert "aarch64-unknown-linux-gnu" in error_lines[0]


@pytest.mark.parametrize(
    ("cargo_toolchain_spec", "extra_args"),
    [
        pytest.param(
            "+bogus-nightly",
            ["+bogus-nightly"],
            id="keeps_toolchain_override",
        ),
        pytest.param("", [], id="omits_toolchain_override"),
    ],
)
def test_cross_container_fallback_cargo_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cargo_toolchain_spec: str,
    extra_args: list[str],
) -> None:
    """Container-error fallback emits the correct cargo argv for the toolchain spec."""
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
        cargo_toolchain_spec=cargo_toolchain_spec,
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

    manifest_path = str(tmp_path / "Cargo.toml")
    expected = [
        "cargo",
        *extra_args,
        "build",
        "--manifest-path",
        manifest_path,
        "--release",
        "--target",
        "aarch64-unknown-linux-gnu",
    ]
    assert calls == [expected]


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
        CrossDecisionConfig(cargo_toolchain_spec="+bogus-nightly"),
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
        CrossDecisionConfig(
            cargo_toolchain_spec="+bogus-nightly",
            requires_cross_container=True,
        ),
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
