"""Resolve what ``install-prover-tools`` installs, caches and keys.

The action decides everything here and downloads nothing: the tool list, the
``rust-prover-tools`` source, each tool's version, cache key and directories.
Decisions live in one pure function so a test can read them without a runner.

The source is a full commit SHA. A tag or branch would let the installer, and
with it every digest the installer verifies, change underneath a consumer.
Digests are not restated here; ``rust-prover-tools`` pins them at that revision
and checks every download against them, so a second table would only drift.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import os
import re
import sys
import tomllib
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

SOURCE_ROOT = "git+https://github.com/leynos/rust-prover-tools@"
TOOLS = ("kani", "verus", "z3")
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.]{0,63}")
_LANE_WORD = re.compile(r"[0-9A-Za-z._-]{1,64}")
_VERSION_FILES = {"kani": "tools/kani/VERSION", "verus": "tools/verus/VERSION"}
SNAPSHOT_PIN = "tools/kani/snapshot.toml"
SNAPSHOT_MODES = ("auto", "true", "false")
# The keys the tool's schema version 1 requires; the tool validates their
# values, and this action only names a pin that cannot be that document.
_PIN_KEYS = frozenset(
    {
        "schema_version",
        "repository",
        "tag",
        "upstream_commit",
        "builder_commit",
        "target",
        "manifest_sha256",
    }
)


class PlanError(ValueError):
    """An input the action refuses, named so the step log says which."""


def _refuse(message: str) -> typ.NoReturn:
    """Refuse the plan with *message*."""
    raise PlanError(message)


@dc.dataclass(frozen=True, slots=True)
class Request:
    """The action's inputs, as the runner supplies them."""

    tools: str
    sha: str
    repo_root: Path
    runner_image: str
    lane: str
    cache_root: Path
    kani_install_dir: str = ""
    verus_install_dir: str = ""
    z3_install_dir: str = ""
    cache: str = "true"
    kani_snapshot: str = "auto"


def _select(tools: str) -> tuple[str, ...]:
    """Return the requested tools in canonical order, adding Z3 for Verus."""
    words = tools.split()
    unknown = sorted(set(words) - set(TOOLS))
    if unknown or not words:
        _refuse(f"tools must name some of {', '.join(TOOLS)}; got {tools!r}")
    wanted = set(words)
    if "verus" in wanted:
        wanted.add("z3")
    return tuple(tool for tool in TOOLS if tool in wanted)


def _read_version(root: Path, tool: str) -> str:
    """Read a tool's version pin from the consumer repository."""
    path = root / _VERSION_FILES[tool]
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        _refuse(f"cannot read {path}: {error.strerror}")
    if not _VERSION.fullmatch(text):
        _refuse(f"{path} does not hold a version: {text!r}")
    return text


def _check_inputs(request: Request) -> None:
    """Refuse a pin that is not a full SHA and lane words a key cannot carry."""
    if not _SHA.fullmatch(request.sha):
        _refuse(
            "prover-tools-sha must be a full 40-character lower-case commit "
            f"SHA, got {request.sha!r}"
        )
    for name, value in (("runner-image", request.runner_image), ("lane", request.lane)):
        if not _LANE_WORD.fullmatch(value):
            _refuse(f"{name} holds characters a cache key cannot carry")


def _keys(request: Request, root: Path, selected: tuple[str, ...]) -> dict[str, str]:
    """Return each selected tool's version and cache key."""
    outputs: dict[str, str] = {}
    short = request.sha[:12]
    for tool in selected:
        # Z3 has no version file: the tool pins its solver per revision, so the
        # revision in the key is its version.
        version = _read_version(root, tool) if tool in _VERSION_FILES else "pinned"
        outputs[f"{tool}-version"] = version
        outputs[f"{tool}-key"] = (
            f"prover-tools-v1-{request.lane}-{request.runner_image}"
            f"-{tool}-{version}-{short}"
        )
    return outputs


def _under(root: Path, given: str, default: Path) -> str:
    """Return *given* as an absolute path, relative to *root* as the tool reads it.

    The tool resolves a relative install directory against the repository
    root, so the shell steps that look in the directory afterwards must use the
    same base rather than the job's working directory.
    """
    return str(root / given) if given else str(default)


def _directories(
    request: Request, root: Path, selected: tuple[str, ...], versions: dict[str, str]
) -> dict[str, str]:
    """Return the cache and install directories each selected tool uses."""
    outputs = {
        f"{tool}-cache-dir": str(request.cache_root / tool)
        for tool in ("kani", "z3")
        if tool in selected
    }
    if "verus" in selected:
        # Version-qualified, because the tool reuses any binary already in the
        # directory it is given, so a bumped pin must land somewhere new.
        parent = request.verus_install_dir or ".verus"
        outputs["verus-install-dir"] = str(root / parent / versions["verus-version"])
    if "z3" in selected:
        default = root / ".verus" / "z3"
        outputs["z3-install-dir"] = _under(root, request.z3_install_dir, default)
    if "kani" in selected:
        outputs["kani-install-dir"] = (
            _under(root, request.kani_install_dir, root)
            if request.kani_install_dir
            else ""
        )
    return outputs


def _check_pin(pin: Path) -> None:
    """Refuse a file that is not a version 1 pin."""
    try:
        data = pin.read_bytes()
    except OSError as error:
        _refuse(f"cannot read {pin}: {error.strerror}")
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        _refuse(f"{pin} is not valid TOML: {error}")
    keys = frozenset(document)
    if keys != _PIN_KEYS:
        missing = ", ".join(sorted(_PIN_KEYS - keys)) or "none"
        unknown = ", ".join(sorted(keys - _PIN_KEYS)) or "none"
        _refuse(f"{pin} is not a snapshot pin: missing {missing}; unknown {unknown}")


def _snapshot(
    request: Request, root: Path, selected: tuple[str, ...]
) -> dict[str, str]:
    """Return the Kani snapshot outputs, deciding whether a pin is in force.

    With no pin and ``kani-snapshot`` left at ``auto``, nothing changes: the
    legacy install runs exactly as before. A pin routes Kani through the
    snapshot installer instead, and then ``tools/kani/VERSION`` is not read.
    """
    mode = request.kani_snapshot
    if mode not in SNAPSHOT_MODES:
        _refuse(f"kani-snapshot must be auto, true or false, got {mode!r}")
    pin = root / SNAPSHOT_PIN
    off = {"kani-snapshot": "false"}
    if (
        "kani" not in selected
        or mode == "false"
        or (mode == "auto" and not pin.is_file())
    ):
        if mode == "true":
            _refuse("kani-snapshot is true but kani is not in tools")
        return off
    _check_pin(pin)
    return {
        "kani-snapshot": "true",
        "kani-snapshot-pin": str(pin),
    }


def plan(request: Request) -> dict[str, str]:
    """Return the step outputs for *request*, or raise ``PlanError``."""
    _check_inputs(request)
    if request.cache not in {"true", "false"}:
        _refuse(f"cache must be true or false, got {request.cache!r}")
    selected = _select(request.tools)
    # Absolute, because the tool resolves a relative install directory against
    # the repository root and a path that already names it would be doubled.
    root = request.repo_root.resolve()
    snapshot = _snapshot(request, root, selected)
    if snapshot["kani-snapshot"] == "true":
        # The legacy Kani install, version file and archive cache all step aside.
        selected = tuple(tool for tool in selected if tool != "kani")
    outputs = {
        "source": f"{SOURCE_ROOT}{request.sha}",
        "tools": " ".join(selected),
        **{tool: str(tool in selected).lower() for tool in TOOLS},
        **{
            f"{tool}-cached": str(tool in selected and request.cache == "true").lower()
            for tool in ("kani", "z3")
        },
    }
    keys = _keys(request, root, selected)
    return {
        **outputs,
        **snapshot,
        **keys,
        **_directories(request, root, selected, keys),
    }


def _emit(outputs: cabc.Mapping[str, str], stream: typ.TextIO) -> None:
    """Write ``key=value`` lines; a value never holds a newline."""
    for key, value in outputs.items():
        if "\n" in value or "\r" in value:
            _refuse(f"output {key} holds a line break")
        stream.write(f"{key}={value}\n")


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Resolve the plan and append it to ``$GITHUB_OUTPUT`` (or stdout)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--kani-install-dir", default="")
    parser.add_argument("--verus-install-dir", default="")
    parser.add_argument("--z3-install-dir", default="")
    parser.add_argument("--cache", default="true")
    parser.add_argument("--kani-snapshot", default="auto")
    args = parser.parse_args(argv)
    request = Request(
        tools=args.tools,
        sha=args.sha,
        repo_root=args.repo_root,
        runner_image=args.runner_image,
        lane=args.lane,
        cache_root=args.cache_root,
        kani_install_dir=args.kani_install_dir,
        verus_install_dir=args.verus_install_dir,
        z3_install_dir=args.z3_install_dir,
        cache=args.cache,
        kani_snapshot=args.kani_snapshot,
    )
    try:
        outputs = plan(request)
    except PlanError as error:
        print(f"::error title=install-prover-tools::{error}", file=sys.stderr)
        return 1
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            _emit(outputs, stream)
    else:
        _emit(outputs, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
