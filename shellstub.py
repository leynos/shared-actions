from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Sequence

@dataclass
class Call:
    argv: list[str]
    cwd: str
    timestamp: str

@dataclass
class Variant:
    match: Sequence[str] | None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

class StubManager:
    def __init__(self, dir_: Path):
        self.dir = dir_
        self.dir.mkdir(parents=True, exist_ok=True)
        self._calls_file = self.dir / "calls.jsonl"
        self._specs: dict[str, dict] = {}

    def register(
        self,
        name: str,
        *,
        variants: list[dict] | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> None:
        if variants is None:
            variants = [dict(match=None, stdout=stdout, stderr=stderr, exit_code=exit_code)]
        for v in variants:
            if callable(v.get("match")):
                raise NotImplementedError("callable matchers not supported")
        spec = {"variants": variants}
        self._specs[name] = spec
        spec_file = self.dir / f"{name}.json"
        spec_file.write_text(json.dumps(spec))
        path = self.dir / name
        path.write_text(self._wrapper_source(name, spec_file))
        path.chmod(0o755)

    def calls_of(self, name: str) -> list[Call]:
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
        return {
            "PATH": f"{self.dir}{os.pathsep}{os.getenv('PATH', '')}",
            "PYTHONPATH": os.getenv("PYTHONPATH", ""),
            **os.environ,
        }

    def _wrapper_source(self, name: str, spec_path: Path) -> str:
        calls_file = str(self._calls_file)
        spec_file = str(spec_path)
        return f"""#!/usr/bin/env python3
import json, os, sys, datetime as _dt

with open({spec_file!r}) as fh:
    spec = json.load(fh)

calls_file = {calls_file!r}
argv = sys.argv[1:]

rec = {{"cmd": {name!r}, "argv": argv, "cwd": os.getcwd(), "ts": _dt.datetime.utcnow().isoformat() + 'Z'}}
with open(calls_file, 'a') as fh:
    json.dump(rec, fh); fh.write('\\n')

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

sys.stdout.write(chosen.get('stdout', ''))
sys.stderr.write(chosen.get('stderr', ''))
sys.exit(chosen.get('exit_code', 0))
"""
