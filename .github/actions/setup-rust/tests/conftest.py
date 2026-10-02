"""Common test utilities for setup-rust scripts."""

from __future__ import annotations

import dataclasses as dc
import os
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
from syspath_hack import find_project_root, prepend_to_syspath

if sys.platform.startswith("win"):
    pytest.skip("cmd-mox IPC is unavailable on Windows", allow_module_level=True)

from setup_rust_test_helpers import get_step, requires_bash

from test_support.cmd_mox_stub_adapter import StubManager

if typ.TYPE_CHECKING:
    from cmd_mox import CmdMox


ROOT = find_project_root(start=Path(__file__).resolve().parent)
prepend_to_syspath(ROOT)


@pytest.fixture
def shell_stubs(cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch) -> StubManager:
    """Return a ``StubManager`` configured for the current test."""
    monkeypatch.setenv("PYTHONPATH", f"{ROOT}{os.pathsep}{os.getenv('PYTHONPATH', '')}")
    with StubManager(cmd_mox) as mgr:
        yield mgr


@dc.dataclass(frozen=True)
class InstallRun:
    """The observable outcome of one run of the clang/lld install fragment."""

    result: subprocess.CompletedProcess[str]
    github_output: list[str]
    sudo_calls: list[str]


class InstallStepRunner(typ.Protocol):
    """Callable returned by the ``run_install_step`` fixture."""

    def __call__(
        self,
        *,
        present: typ.Iterable[str] = ("clang", "ld.lld"),
        fail_on: str | None = None,
    ) -> InstallRun:
        """Run the install step with *present* tools and *fail_on* failing."""
        ...


@pytest.fixture
def run_install_step(tmp_path: Path) -> InstallStepRunner:
    """Return a runner for setup-rust's ``Install clang and lld`` fragment.

    The fragment runs under bash with a stubbed ``sudo`` that records each call
    to a log and, when asked, fails one ``apt-get`` subcommand. PATH is exactly
    the stub directory plus a directory of stand-in tools, with the host's own
    PATH deliberately excluded: a host that ships a real ``clang`` or
    ``ld.lld`` (common on Linux dev images) would otherwise make the "missing"
    cases pass by accident.
    """

    def run(
        *,
        present: typ.Iterable[str] = ("clang", "ld.lld"),
        fail_on: str | None = None,
    ) -> InstallRun:
        bash = requires_bash()
        stubs_dir = tmp_path / "stubs"
        tool_dir = tmp_path / "tools"
        for directory in (stubs_dir, tool_dir):
            directory.mkdir(parents=True, exist_ok=True)
        log = stubs_dir / "sudo.log"
        # An absolute shebang: the restricted PATH cannot resolve ``env bash``.
        lines = [f"#!{bash}", f'echo "$*" >> "{log}"']
        if fail_on:
            lines.append(f'[ "$2" = "{fail_on}" ] && exit 100')
        lines.append("exit 0")
        sudo = stubs_dir / "sudo"
        sudo.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sudo.chmod(0o755)
        for name in present:
            tool = tool_dir / name
            tool.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)
        github_output = tmp_path / "github-output"
        github_output.touch()
        env = {
            **os.environ,
            "PATH": os.pathsep.join([str(tool_dir), str(stubs_dir)]),
            "GITHUB_OUTPUT": str(github_output),
        }
        result = subprocess.run(  # noqa: S603, TID251 - exercise the action fragment.
            [bash, "-c", str(get_step("Install clang and lld")["run"])],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return InstallRun(
            result=result,
            github_output=github_output.read_text(encoding="utf-8").splitlines(),
            sudo_calls=calls,
        )

    return run
