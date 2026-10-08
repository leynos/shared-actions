"""Shared harness for the black-box tests: a fake uv, a repo and a cache."""

from __future__ import annotations

import dataclasses as dc
import json
import os
import shutil
import stat
import subprocess
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "uv_gate.py"
FIXTURES = HERE / "fixtures"


def fixture_text(name: str) -> str:
    """Return the recorded uv output stored in ``fixtures/<name>.txt``."""
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def response(rc: int, stderr: str = "", stdout: str = "") -> dict[str, object]:
    """Build one scripted fake-uv response."""
    return {"rc": rc, "stderr": stderr, "stdout": stdout}


def rule(
    startswith: list[str],
    *responses: dict[str, object],
    without: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build a scenario rule matching an argv prefix."""
    return {
        "startswith": startswith,
        "without": list(without),
        "responses": list(responses),
    }


@dc.dataclass
class Result:
    """The outcome of running the helper against the fake uv."""

    status: int
    stdout: str
    stderr: str
    log_path: Path

    def uv_calls(self) -> list[dict[str, typ.Any]]:
        """Return the fake uv's recorded calls, excluding the cache query."""
        if not self.log_path.exists():
            return []
        entries = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        return [e for e in entries if e["argv"][:1] != ["--no-config"]]

    def argvs(self) -> list[list[str]]:
        """Return only the argument vectors of the recorded calls."""
        return [call["argv"] for call in self.uv_calls()]


class Harness:
    """Lay out a repository, a fake uv on PATH and a cache directory."""

    def __init__(self, root: Path) -> None:
        """Create the directories under ``root``."""
        self.root = root
        self.repo = root / "repo"
        self.bin = root / "bin"
        self.home = root / "home"
        self.cache = root / "cache"
        for directory in (self.repo, self.bin, self.home):
            directory.mkdir()
        fake = self.bin / "uv"
        fake.write_text(
            f"#!{sys.executable}\n"
            + (HERE / "fake_uv.py").read_text(encoding="utf-8").split("\n", 1)[1],
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = root / "uv.log"
        self.scenario = root / "scenario.json"

    def run(
        self,
        args: cabc.Sequence[str],
        rules: list[dict[str, object]] | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> Result:
        """Run the helper with ``args`` against the scripted fake uv."""
        self.scenario.write_text(json.dumps(rules or []), encoding="utf-8")
        environment = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
            "HOME": str(self.home),
            "FAKE_UV_LOG": str(self.log),
            "FAKE_UV_SCENARIO": str(self.scenario),
            "FAKE_UV_CACHE": str(self.cache),
            **(env or {}),
        }
        process = subprocess.Popen(  # noqa: S603  # fixed argv, test-only
            [sys.executable, str(GATE), *args],
            cwd=self.repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=30)
        return Result(process.returncode, stdout, stderr, self.log)


def other_device_dir(than: Path) -> Path | None:
    """Return a writable directory on a different device from ``than``, if any."""
    candidate = Path("/dev/shm")  # noqa: S108  # tmpfs probe, not a temp file
    if not candidate.is_dir() or not os.access(candidate, os.W_OK):
        return None
    if candidate.stat().st_dev == than.stat().st_dev:
        return None
    return candidate


def remove_tree(path: Path) -> None:
    """Remove a directory made by a test."""
    shutil.rmtree(path, ignore_errors=True)
