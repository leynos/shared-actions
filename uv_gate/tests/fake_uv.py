#!/usr/bin/env python3
"""A scripted stand-in for ``uv`` used by the black-box tests.

Every call is appended to ``FAKE_UV_LOG`` as one JSON line. Responses come
from the JSON scenario in ``FAKE_UV_SCENARIO``: a list of rules, each with a
``startswith`` argv prefix and a list of ``responses`` consumed in call order
(the last one repeats). ``--no-config cache dir`` answers ``FAKE_UV_CACHE``.
"""

import json
import os
import shutil
import sys
from pathlib import Path

WATCHED = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "BASH_ENV",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "UV_LINK_MODE",
    "GIT_TERMINAL_PROMPT",
    "UV_OFFLINE",
    "UV_NO_CACHE",
    "UV_REFRESH",
)

argv = sys.argv[1:]
log = Path(os.environ["FAKE_UV_LOG"])
previous = (
    [json.loads(line)["argv"] for line in log.read_text().splitlines()]
    if log.exists()
    else []
)
entry = {
    "argv": argv,
    "env": {key: os.environ.get(key) for key in WATCHED},
    "path": os.environ.get("PATH", ""),
    "git": os.path.realpath(shutil.which("git") or ""),
}
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\n")

if argv[:3] == ["--no-config", "cache", "dir"]:
    sys.stdout.write(os.environ["FAKE_UV_CACHE"] + "\n")
    sys.exit(0)


def matches(call: list[str], rule: dict) -> bool:
    """Report whether ``call`` starts with the rule's prefix and has no banned flag."""
    prefix = rule["startswith"]
    banned = rule.get("without", [])
    return call[: len(prefix)] == prefix and not any(flag in call for flag in banned)


rules = json.loads(Path(os.environ["FAKE_UV_SCENARIO"]).read_text())
for rule in rules:
    if matches(argv, rule):
        index = sum(1 for call in previous if matches(call, rule))
        responses = rule["responses"]
        response = responses[min(index, len(responses) - 1)]
        sys.stdout.write(response.get("stdout", ""))
        sys.stderr.write(response.get("stderr", ""))
        sys.exit(response.get("rc", 0))
sys.stderr.write(f"fake uv: no rule for {argv}\n")
sys.exit(97)
