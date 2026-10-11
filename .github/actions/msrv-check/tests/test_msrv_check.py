"""Unit tests for the msrv-check action's script.

The script runs against stub `rustup` and `cargo` commands that record their
arguments and the `RUSTFLAGS` they were given, so each test checks what the job
would ask the toolchain to do without installing anything.
"""

from __future__ import annotations

import os
import stat
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import msrv_check

_STUB = """#!/bin/sh
name=$(basename "$0")
printf '%s %s [RUSTFLAGS=%s]\\n' "$name" "$*" "${RUSTFLAGS-unset}" >> "$STUB_LOG"
exit "${STUB_EXIT:-0}"
"""


class Harness(typ.NamedTuple):
    """A checkout with stub toolchain commands and the environment to run in."""

    root: Path
    log: Path
    env: dict[str, str]

    def calls(self) -> list[str]:
        """Return what the stubs recorded, one entry per command."""
        return self.log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Provide a checkout directory, stub commands and a clean action env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("rustup", "cargo"):
        stub = bin_dir / name
        stub.write_text(_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    root = tmp_path / "checkout"
    root.mkdir()
    log = tmp_path / "calls.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    env = {
        "PATH": os.environ["PATH"],
        "STUB_LOG": str(log),
        "INPUT_WORKING_DIRECTORY": str(root),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    return Harness(root, log, env)


def _write(root: Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *root*."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_declared_msrv_is_installed_and_checked(harness: Harness) -> None:
    """The toolchain comes from the manifest and the check is one cargo check."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\nrust-version = "1.86"\n')

    status = msrv_check.main(harness.env)

    manifest = (harness.root / "Cargo.toml").resolve()
    assert status == 0, harness.calls()
    assert harness.calls() == [
        "rustup toolchain install 1.86 --profile minimal --no-self-update [RUSTFLAGS=]",
        (
            f"cargo +1.86 check --manifest-path={manifest} "
            "--lib --locked --all-features [RUSTFLAGS=]"
        ),
    ], harness.calls()
    assert "msrv=1.86" in Path(harness.env["GITHUB_OUTPUT"]).read_text(encoding="utf-8")


def test_a_workspace_member_inherits_the_workspace_value(harness: Harness) -> None:
    """`rust-version.workspace = true` resolves from the enclosing workspace."""
    _write(harness.root, "Cargo.toml", '[workspace.package]\nrust-version = "1.88"\n')
    _write(
        harness.root,
        "crates/a/Cargo.toml",
        '[package]\nname = "a"\nrust-version.workspace = true\n',
    )
    harness.env["INPUT_MANIFEST_PATH"] = "crates/a/Cargo.toml"

    status = msrv_check.main(harness.env)

    assert status == 0, harness.calls()
    assert harness.calls()[-1].startswith("cargo +1.88 check"), harness.calls()


def test_a_missing_rust_version_fails_before_any_toolchain_call(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure names the cause and nothing is installed."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\n')

    status = msrv_check.main(harness.env)

    assert status == 1, harness.calls()
    assert harness.calls() == [], harness.calls()
    assert "declares no rust-version" in capsys.readouterr().err


def test_a_missing_manifest_fails_with_its_path(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent manifest is a named error."""
    status = msrv_check.main(harness.env)

    assert status == 1, harness.calls()
    assert "does not exist" in capsys.readouterr().err


def test_the_cargo_config_is_dropped_by_default(harness: Harness) -> None:
    """The config goes from the runner checkout, and RUSTFLAGS is set empty."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\nrust-version = "1.86"\n')
    _write(
        harness.root,
        ".cargo/config.toml",
        '[profile.dev]\ncodegen-backend = "cranelift"\n',
    )

    status = msrv_check.main(harness.env)

    assert status == 0, harness.calls()
    assert not (harness.root / ".cargo" / "config.toml").exists()
    assert all("[RUSTFLAGS=]" in call for call in harness.calls()), harness.calls()


@pytest.mark.parametrize("value", ["true", "1", "yes", "ON"])
def test_keep_cargo_config_leaves_the_config_and_rustflags(
    harness: Harness, value: str
) -> None:
    """With the input truthy, the config stays and RUSTFLAGS is not overridden."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\nrust-version = "1.86"\n')
    _write(harness.root, ".cargo/config", "[build]\n")
    harness.env["INPUT_KEEP_CARGO_CONFIG"] = value

    status = msrv_check.main(harness.env)

    assert status == 0, harness.calls()
    assert (harness.root / ".cargo" / "config").exists()
    assert all("[RUSTFLAGS=unset]" in call for call in harness.calls()), harness.calls()


def test_cargo_args_are_passed_through(harness: Harness) -> None:
    """The input replaces the default arguments."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\nrust-version = "1.86"\n')
    harness.env["INPUT_CARGO_ARGS"] = "-p x --locked"

    msrv_check.main(harness.env)

    assert harness.calls()[-1].endswith("-p x --locked [RUSTFLAGS=]"), harness.calls()


def test_a_failing_check_returns_its_status(harness: Harness) -> None:
    """The job fails when cargo does, and install failures stop before the check."""
    _write(harness.root, "Cargo.toml", '[package]\nname = "x"\nrust-version = "1.86"\n')
    harness.env["STUB_EXIT"] = "3"

    status = msrv_check.main(harness.env)

    assert status == 3, harness.calls()
    assert len(harness.calls()) == 1, harness.calls()
