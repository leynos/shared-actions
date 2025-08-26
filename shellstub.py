"""Minimal framework for stubbing command line tools in tests."""

from __future__ import annotations

import collections.abc as cabc  # noqa: TC003 - used at runtime
import dataclasses as dc
import json
import os
import sys  # used by Windows launcher
from pathlib import Path  # noqa: TC003 - used at runtime


@dc.dataclass
class Call:
    """Record of a stub invocation."""

    argv: list[str]
    cwd: str
    timestamp: str


@dc.dataclass
class Variant:
    """Possible behaviour for a stub based on its arguments."""

    match: cabc.Sequence[str] | None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dc.dataclass
class StubSpec:
    """Specification for a stub including its variants and optional callback."""

    variants: list[Variant]
    func: cabc.Callable[[cabc.Sequence[str]], int] | None = None


class StubManager:
    """Manage command stubs and their recorded invocations."""

    def __init__(self, dir_: Path) -> None:
        """Create a manager whose wrappers live under ``dir_``."""
        self.dir = dir_
        self.dir.mkdir(parents=True, exist_ok=True)
        self._calls_file = self.dir / "calls.jsonl"
        self._specs: dict[str, StubSpec] = {}

    def register(
        self,
        name: str,
        *,
        variants: list[dict] | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        func: cabc.Callable[[cabc.Sequence[str]], int] | None = None,
    ) -> None:
        """Register a new stub with either a variants list or single behaviour."""
        if variants is None:
            variants = [
                {
                    "match": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                }
            ]
        parsed = [
            Variant(
                match=v["match"],
                stdout=v.get("stdout", ""),
                stderr=v.get("stderr", ""),
                exit_code=v.get("exit_code", 0),
            )
            for v in variants
        ]
        spec = StubSpec(parsed, func=func)
        self._specs[name] = spec
        spec_file = self.dir / f"{name}.json"
        spec_file.write_text(json.dumps({"variants": [dc.asdict(v) for v in parsed]}))
        if os.name == "nt":
            script_path = self.dir / f"{name}.py"
            script_path.write_text(self._wrapper_source(name, spec_file))
            cmd_path = self.dir / f"{name}.cmd"
            cmd_path.write_text(
                f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n'
            )
            cmd_path.chmod(0o755)
        else:
            path = self.dir / name
            path.write_text(self._wrapper_source(name, spec_file))
            path.chmod(0o755)

    def calls_of(self, name: str) -> list[Call]:
        """Return the list of recorded calls for *name* in order."""
        out: list[Call] = []
        if not self._calls_file.exists():
            return out
        for line in self._calls_file.read_text().splitlines():
            rec = json.loads(line)
            if rec["cmd"] == name:
                out.append(Call(rec["argv"], rec["cwd"], rec["ts"]))
        return out

    @property
    def env(self) -> dict[str, str]:
        """Return environment variables that expose the stub directory."""
        env = dict(os.environ)
        env["PATH"] = f"{self.dir}{os.pathsep}{env.get('PATH', '')}"
        env["PYTHONPATH"] = env.get("PYTHONPATH", "")
        return env

    def _wrapper_source(self, name: str, spec_path: Path) -> str:
        """Return the Python wrapper source code for *name*."""
        calls_file = str(self._calls_file)
        spec_file = str(spec_path)
        return f"""#!/usr/bin/env python3
import importlib, json, os, sys, datetime as _dt

mgr = importlib.import_module('shellstub')._GLOBAL_MANAGER
func = None
if mgr and {name!r} in mgr._specs:
    func = mgr._specs[{name!r}].func

with open({spec_file!r}) as fh:
    spec = json.load(fh)

calls_file = {calls_file!r}
argv = sys.argv[1:]

rec = {{
    "cmd": {name!r},
    "argv": argv,
    "cwd": os.getcwd(),
    "ts": _dt.datetime.utcnow().isoformat() + 'Z',
}}
with open(calls_file, 'a') as fh:
    json.dump(rec, fh)
    fh.write('\\n')

if func:
    sys.exit(func(argv))

chosen = None
for var in spec['variants']:
    m = var.get('match')
    if m is None:
        chosen = chosen or var
    elif m == argv:
        chosen = var
        break

if chosen is None:
    sys.exit(127)

out = chosen.get('stdout', '')
err = chosen.get('stderr', '')
sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(chosen.get('exit_code', 0))
"""


# single mutable reference, monkey-patched by tests
_GLOBAL_MANAGER: StubManager | None = None
