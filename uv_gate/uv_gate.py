#!/usr/bin/env python3
"""Run ``uv`` reliably: clean environment, global cache, offline gates.

``uv_gate.py`` is a single stdlib-only file that repositories vendor
byte-for-byte as ``scripts/uv_gate.py``. It implements the procedure in
``uv-robust-execution.md``:

* ``prepare`` runs ``uv sync --locked --offline``. Only when uv proves that a
  required file is missing from the cache does it run **one** online
  ``uv sync --locked``.
* ``run`` runs ``uv run --frozen --offline <command>``. It never goes online
  and never retries.
* ``tool`` runs ``uv tool run --offline`` for a version-pinned tool. Like
  ``prepare``, it goes online once, and only for a proven cache miss.

Every invocation uses a cleaned environment (no inherited Git configuration,
tokens or ``BASH_ENV``, no ``~/.lody`` on ``PATH``, no Git prompts, and
``/usr/bin/git`` for Git-backed fetches) and the global uv cache. When the
cache and the project's virtual environment are on different filesystems it
sets ``UV_LINK_MODE=copy``.

The helper never refreshes a lock file, never purges a cache and never
retries a failed test, stale lock, authentication error or missing package.
Its exit status is uv's exit status; its own refusals exit with status 2.
Failures print one ``uv-gate: <class>: ...`` line on standard error after uv's
own output, which is passed through unchanged.

Python 3.9 or later is enough: the file uses only the standard library.
"""

from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REFUSAL_STATUS = 2
ALLOW_ONLINE_VARIABLE = "UV_GATE_ALLOW_ONLINE"
SYSTEM_GIT = Path("/usr/bin/git")
TAIL_BYTES = 65536

# Inherited uv switches that would override the gate's own policy: offline or
# no-cache state defeats the online step and the global cache, and the
# refresh, frozen and locked switches change freshness behind the gate's back.
STRIPPED_VARIABLES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "BASH_ENV",
        "UV_CACHE_DIR",
        "UV_TOOL_DIR",
        "UV_OFFLINE",
        "UV_NO_CACHE",
        "UV_FROZEN",
        "UV_LOCKED",
    }
)
STRIPPED_PREFIXES = ("GIT_CONFIG_", "UV_REFRESH", "UV_UPGRADE")

# Flags that would make a gate refresh, upgrade or purge instead of read the
# prepared environment. The procedure forbids all of them as automatic recovery.
FORBIDDEN_FLAGS = (
    "--no-offline",
    "--no-cache",
    "--no-frozen",
    "--no-locked",
    "--locked",
    "--frozen",
    "--offline",
)
# Whole families, so ``--refresh-package`` and friends are refused too.
FORBIDDEN_PREFIXES = ("--refresh", "--upgrade", "--reinstall")
# Short aliases: ``-U`` (upgrade), ``-P`` (upgrade package), ``-n`` (no cache).
FORBIDDEN_SHORT = "UPn"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
PINNED_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]+\])?")
PINNED_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]*")


class Kind(enum.Enum):
    """The classes of uv failure that the helper tells apart."""

    CACHE_MISS = "cache-miss"
    OFFLINE_RESOLUTION = "offline-resolution"
    STALE_LOCK = "stale-lock"
    MISSING_LOCK = "missing-lock"
    MISSING_PACKAGE = "missing-package"
    MISSING_REVISION = "missing-revision"
    AUTH = "auth"
    UNKNOWN = "unknown"


# Order matters: the first matching signature wins. Whitespace is matched
# loosely because uv wraps its messages at the terminal width.
SIGNATURES: tuple[tuple[Kind, re.Pattern[str]], ...] = (
    (Kind.STALE_LOCK, re.compile(r"lockfile\s+at\s+`?uv\.lock`?\s+needs\s+to\s+be")),
    (Kind.MISSING_LOCK, re.compile(r"Unable\s+to\s+find\s+lockfile")),
    (
        Kind.CACHE_MISS,
        re.compile(
            r"Network\s+connectivity\s+is\s+disabled,\s+but\s+the\s+requested\s+data"
            r"\s+wasn't\s+found\s+in\s+the\s+cache"
        ),
    ),
    (
        Kind.AUTH,
        re.compile(
            r"terminal\s+prompts\s+disabled|could\s+not\s+read\s+(?:Username|Password)"
            r"|Authentication\s+failed|Permission\s+denied\s+\(publickey\)"
            r"|Repository\s+not\s+found|\b40[13]\s+(?:Unauthorized|Forbidden)"
            r"|status\s+code\s+40[13]",
            re.IGNORECASE,
        ),
    ),
    (
        Kind.MISSING_REVISION,
        re.compile(
            r"failed\s+to\s+find\s+branch,\s+tag,\s+or\s+commit"
            r"|unknown\s+revision\s+or\s+path"
        ),
    ),
    (
        Kind.MISSING_PACKAGE,
        re.compile(r"was\s+not\s+found\s+in\s+the\s+package\s+registry"),
    ),
    (
        Kind.OFFLINE_RESOLUTION,
        re.compile(
            r"not\s+found\s+in\s+the\s+cache(?s:.*)network\s+was\s+disabled"
            r"|network\s+was\s+disabled(?s:.*)not\s+found\s+in\s+the\s+cache"
        ),
    ),
)

ADVICE = {
    Kind.CACHE_MISS: "a required file is not in the uv cache",
    Kind.OFFLINE_RESOLUTION: (
        "offline resolution needs data that is not in the uv cache"
    ),
    Kind.STALE_LOCK: (
        "uv.lock is out of date. Run `uv lock` yourself and commit it; the gate "
        "never refreshes a lock file"
    ),
    Kind.MISSING_LOCK: (
        "uv.lock is missing. Create it with `uv lock`, commit it, and run again"
    ),
    Kind.MISSING_PACKAGE: (
        "a package or version does not exist in the registry; not retried"
    ),
    Kind.MISSING_REVISION: (
        "a Git revision does not exist at the pinned source; not retried"
    ),
    Kind.AUTH: (
        "authentication failed or the repository is not accessible with the "
        "current credentials; not retried. Fix the credential, not the gate"
    ),
    Kind.UNKNOWN: "uv failed for a reason the gate does not retry",
}


class GateError(Exception):
    """A refusal by the helper itself, reported with exit status 2."""


def say(message: str) -> None:
    """Print one ``uv-gate:`` line on standard error."""
    sys.stderr.write(f"uv-gate: {message}\n")
    sys.stderr.flush()


def classify(text: str) -> Kind:
    """Name the class of uv failure shown in ``text``.

    Parameters
    ----------
    text:
        The tail of uv's standard error.

    Returns
    -------
    Kind
        The first matching class, or ``Kind.UNKNOWN`` when none matches.

    Examples
    --------
    >>> classify("Unable to find lockfile at `uv.lock`.") is Kind.MISSING_LOCK
    True
    >>> classify("everything exploded") is Kind.UNKNOWN
    True
    """
    for kind, pattern in SIGNATURES:
        if pattern.search(text):
            return kind
    return Kind.UNKNOWN


def clean_environment(environ: cabc.Mapping[str, str], home: Path) -> dict[str, str]:
    """Return ``environ`` without inherited Git, token and uv-location state.

    Parameters
    ----------
    environ:
        The environment to clean; it is not modified.
    home:
        The home directory whose ``.lody`` tree is removed from ``PATH``.

    Returns
    -------
    dict[str, str]
        A new environment with ``GIT_TERMINAL_PROMPT=0`` set.
    """
    env = {
        key: value
        for key, value in environ.items()
        if key not in STRIPPED_VARIABLES and not key.startswith(STRIPPED_PREFIXES)
    }
    lody = str(home / ".lody")
    env["PATH"] = os.pathsep.join(
        item
        for item in env.get("PATH", "").split(os.pathsep)
        if item != lody and not item.startswith(lody + os.sep)
    )
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def device_of(path: Path) -> int:
    """Return the device number (``st_dev``) of ``path``.

    Raises
    ------
    OSError
        When ``path`` cannot be inspected; ``build_context`` turns this into a
        ``GateError`` at the command boundary.
    """
    return path.stat().st_dev


def needs_copy_mode(
    cache: Path,
    environment_path: Path,
    repo: Path,
    device: cabc.Callable[[Path], int] = device_of,
) -> bool:
    """Report whether the cache and the project environment differ in device.

    ``uv`` hard-links cached files when the cache and the environment share a
    filesystem and copies them otherwise. The environment's own device is used
    when it exists, since a ``.venv`` may be a symlink to another filesystem.

    Parameters
    ----------
    cache:
        The global cache directory.
    environment_path:
        The project's virtual environment, which may not exist yet.
    repo:
        The repository root, used when the environment does not exist.
    device:
        Returns the device number of a resolved path. Tests inject it to feed
        two different devices without a second filesystem.

    Returns
    -------
    bool
        ``True`` when ``UV_LINK_MODE=copy`` is required.

    Raises
    ------
    OSError
        When a device query fails (propagated from ``device``).
    """
    device_path = (
        environment_path.resolve() if environment_path.exists() else repo.resolve()
    )
    return device(device_path) != device(cache.resolve())


class Context:
    """The cleaned environment, the located uv and the selected cache."""

    def __init__(self, env: dict[str, str], uv: str, cache: Path) -> None:
        """Hold the resolved execution context."""
        self.env = env
        self.uv = uv
        self.cache = cache
        self._shim_dir: str | None = None

    def install_git_shim(self) -> None:
        """Put ``/usr/bin/git`` first on ``PATH`` through a one-file shim dir."""
        if not (SYSTEM_GIT.is_file() and os.access(SYSTEM_GIT, os.X_OK)):
            return
        self._shim_dir = tempfile.mkdtemp(prefix="uv-gate-git-")
        (Path(self._shim_dir) / "git").symlink_to(SYSTEM_GIT)
        self.env["PATH"] = os.pathsep.join([self._shim_dir, self.env["PATH"]])

    def close(self) -> None:
        """Remove the Git shim directory, if one was made."""
        if self._shim_dir is not None:
            shutil.rmtree(self._shim_dir, ignore_errors=True)
            self._shim_dir = None


def _query_cache_dir(uv: str, env: dict[str, str]) -> Path:
    """Ask uv for the global cache directory with no config and no override."""
    try:
        process = subprocess.Popen(  # noqa: S603  # fixed argv, no shell
            [uv, "--no-config", "cache", "dir"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
    except OSError as exc:
        message = f"cannot run {uv} to find the cache directory: {exc}"
        raise GateError(message) from exc
    if process.returncode != 0 or not stdout.strip():
        message = (
            f"`{uv} --no-config cache dir` failed with status {process.returncode}: "
            f"{stderr.strip()}"
        )
        raise GateError(message)
    return Path(stdout.strip())


def build_context(
    environ: cabc.Mapping[str, str],
    repo: Path,
    home: Path | None = None,
    device: cabc.Callable[[Path], int] = device_of,
) -> Context:
    """Build the cleaned environment, locate uv and select the cache.

    Parameters
    ----------
    environ:
        The inherited environment.
    repo:
        The repository root (normally the current directory).
    home:
        The home directory, defaulting to ``Path.home()``.
    device:
        Returns a path's device number; injected by tests, see
        ``needs_copy_mode``.

    Returns
    -------
    Context
        The ready context. Call ``Context.close`` when finished.

    Raises
    ------
    GateError
        When uv is absent from the cleaned ``PATH``, the cache directory
        cannot be found or created, or the filesystems cannot be compared.
    """
    env = clean_environment(environ, home or Path.home())
    found = shutil.which("uv", path=env["PATH"])
    if found is None:
        message = "uv is not available on the cleaned PATH; install uv and retry"
        raise GateError(message)
    uv = str(Path(found).resolve())
    cache = _query_cache_dir(uv, env)
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        message = f"cannot create the uv cache directory {cache}: {exc}"
        raise GateError(message) from exc
    env["UV_CACHE_DIR"] = str(cache)
    configured = env.get("UV_PROJECT_ENVIRONMENT")
    environment_path = (repo / configured) if configured else repo / ".venv"
    try:
        copy_mode = needs_copy_mode(cache, environment_path, repo, device)
    except OSError as exc:
        message = f"cannot compare the filesystems of the uv cache and project: {exc}"
        raise GateError(message) from exc
    if copy_mode:
        env["UV_LINK_MODE"] = "copy"
    context = Context(env, uv, cache)
    context.install_git_shim()
    return context


def _run_streaming(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run ``argv``, passing standard error through while keeping its tail."""
    tail = bytearray()
    try:
        process = subprocess.Popen(  # noqa: S603  # fixed argv, no shell
            argv, env=env, stderr=subprocess.PIPE
        )
    except OSError as exc:
        message = f"cannot start {argv[0]}: {exc}"
        raise GateError(message) from exc
    stream = process.stderr
    if stream is None:  # pragma: no cover - PIPE was requested
        return process.wait(), ""

    def pump() -> None:
        """Copy the child's standard error to ours, keeping the last bytes."""
        while chunk := os.read(stream.fileno(), 4096):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            tail.extend(chunk)
            del tail[:-TAIL_BYTES]

    reader = threading.Thread(target=pump)
    reader.start()
    try:
        status = process.wait()
    except BaseException:
        process.terminate()
        raise
    finally:
        reader.join()
    return status, tail.decode("utf-8", errors="replace")


def _online_allowed(env: cabc.Mapping[str, str]) -> bool:
    """Report whether the single bounded online step may run."""
    return env.get(ALLOW_ONLINE_VARIABLE, "1") != "0"


TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def describe_spec(spec: str) -> str:
    """Name a tool specification without its URL, credentials or version.

    A pinned Git specification can carry URL userinfo or query strings, so
    logs and refusals use only the package name, or a fixed label for Git URLs.
    """
    if spec.startswith("git+"):
        return "a git+ URL"
    found = TOOL_NAME.match(spec)
    return found.group(0) if found else "an unnamed tool"


def _report(kind: Kind, status: int, *, where: str) -> None:
    """Explain a failure that the helper will not retry."""
    say(f"{kind.value}: {where} failed with status {status}. {ADVICE[kind]}.")


def _refuse_forbidden(arguments: cabc.Sequence[str]) -> None:
    """Refuse flags that would refresh, upgrade or go online implicitly."""
    for argument in arguments:
        name = argument.split("=", 1)[0]
        short = (
            len(name) > 1
            and name[0] == "-"
            and name[1] != "-"
            and name[1] in FORBIDDEN_SHORT
        )
        if name in FORBIDDEN_FLAGS or name.startswith(FORBIDDEN_PREFIXES) or short:
            message = (
                f"{name} is not allowed: the gate controls locking, freshness "
                "and network use itself"
            )
            raise GateError(message)


def prepare(ctx: Context, sync_args: cabc.Sequence[str]) -> int:
    """Prepare dependencies offline, going online once for a proven cache miss.

    Parameters
    ----------
    ctx:
        The execution context.
    sync_args:
        Extra ``uv sync`` arguments (groups, extras, Python version) that the
        gates must also use.

    Returns
    -------
    int
        uv's exit status.
    """
    _refuse_forbidden(sync_args)
    offline = [ctx.uv, "sync", "--locked", "--offline", *sync_args]
    status, text = _run_streaming(offline, ctx.env)
    if status == 0:
        return 0
    kind = classify(text)
    if kind not in {Kind.CACHE_MISS, Kind.OFFLINE_RESOLUTION}:
        _report(kind, status, where="uv sync --locked --offline")
        return status
    if not _online_allowed(ctx.env):
        say(
            f"{kind.value}: {ADVICE[kind]}; the online retry is disabled by "
            f"{ALLOW_ONLINE_VARIABLE}=0."
        )
        return status
    say(f"{kind.value}: {ADVICE[kind]}; running one online `uv sync --locked`.")
    online = [ctx.uv, "sync", "--locked", *sync_args]
    status, text = _run_streaming(online, ctx.env)
    if status != 0:
        _report(classify(text), status, where="uv sync --locked")
    return status


def run_gate(
    ctx: Context, uv_args: cabc.Sequence[str], command: cabc.Sequence[str]
) -> int:
    """Run ``command`` in the prepared environment, offline and frozen.

    Parameters
    ----------
    ctx:
        The execution context.
    uv_args:
        Extra ``uv run`` arguments (groups, extras, Python version).
    command:
        The command to run.

    Returns
    -------
    int
        The command's exit status.

    Raises
    ------
    GateError
        When no command is given or a forbidden flag is passed.
    """
    _refuse_forbidden(uv_args)
    if not command:
        message = "run needs a command after `--`"
        raise GateError(message)
    argv = [ctx.uv, "run", "--frozen", "--offline", *uv_args, *command]
    status, text = _run_streaming(argv, ctx.env)
    if status != 0:
        kind = classify(text)
        if kind is not Kind.UNKNOWN:
            say(f"{kind.value}: {ADVICE[kind]}. Run `prepare` first if needed.")
    return status


def tool_spec(tool_args: cabc.Sequence[str], command: cabc.Sequence[str]) -> str:
    """Return the tool specification that ``uv tool run`` would execute.

    Parameters
    ----------
    tool_args:
        Options placed before ``--``; ``--from SPEC`` names the package.
    command:
        The words after ``--``; the first is the executable.

    Returns
    -------
    str
        The ``--from`` value, or the executable when ``--from`` is absent.

    Raises
    ------
    GateError
        When there is nothing to run.
    """
    for index, argument in enumerate(tool_args):
        if argument == "--from" and index + 1 < len(tool_args):
            return tool_args[index + 1]
        if argument.startswith("--from="):
            return argument.split("=", 1)[1]
    if not command:
        message = "tool needs `--from SPEC` or a command after `--`"
        raise GateError(message)
    return command[0]


def is_pinned(spec: str) -> bool:
    """Report whether ``spec`` pins an exact version or a full commit.

    Accepted: ``name==1.2.3``, ``name@1.2.3`` and ``git+URL@<40 hex digits>``.
    Rejected: bare names, ranges, ``@latest`` and Git refs that are not a full
    commit SHA.

    Examples
    --------
    >>> is_pinned("ruff==0.16.4"), is_pinned("typos@1.2.3"), is_pinned("ruff")
    (True, True, False)
    >>> is_pinned("cibuildwheel>=2.16"), is_pinned("tool@latest")
    (False, False)
    """
    if spec.startswith("git+"):
        _, _, ref = spec.rpartition("@")
        return bool(FULL_SHA.fullmatch(ref.split("#", 1)[0]))
    return any(_is_exact_pin(spec, separator) for separator in ("==", "@"))


def _is_exact_pin(spec: str, separator: str) -> bool:
    """Report whether ``spec`` is ``name<separator>version`` with a real version."""
    name, found, version = spec.partition(separator)
    if not found or version == "latest":
        return False
    return bool(PINNED_NAME.fullmatch(name) and PINNED_VERSION.fullmatch(version))


def tool(
    ctx: Context, tool_args: cabc.Sequence[str], command: cabc.Sequence[str]
) -> int:
    """Run a pinned tool offline, warming it online once on a proven miss.

    Parameters
    ----------
    ctx:
        The execution context.
    tool_args:
        Options for ``uv tool run`` placed before ``--``.
    command:
        The executable and its arguments.

    Returns
    -------
    int
        The tool's exit status.

    Raises
    ------
    GateError
        When the tool specification is not pinned.
    """
    _refuse_forbidden(tool_args)
    spec = tool_spec(tool_args, command)
    if not is_pinned(spec):
        message = (
            f"tool spec for {describe_spec(spec)} is not pinned; use "
            "name==VERSION, name@VERSION "
            "or git+URL@<full commit SHA>"
        )
        raise GateError(message)
    base = [ctx.uv, "tool", "run"]
    status, text = _run_streaming([*base, "--offline", *tool_args, *command], ctx.env)
    if status == 0:
        return 0
    kind = classify(text)
    if kind not in {Kind.CACHE_MISS, Kind.OFFLINE_RESOLUTION}:
        _report(kind, status, where="uv tool run --offline")
        return status
    if not _online_allowed(ctx.env):
        say(
            f"{kind.value}: {ADVICE[kind]}; the online retry is disabled by "
            f"{ALLOW_ONLINE_VARIABLE}=0."
        )
        return status
    say(f"{kind.value}: {ADVICE[kind]}; warming {describe_spec(spec)} online once.")
    status, text = _run_streaming([*base, *tool_args, *command], ctx.env)
    if status != 0:
        _report(classify(text), status, where="uv tool run")
    return status


USAGE = """\
usage: uv_gate.py prepare [UV_SYNC_ARGS...]
       uv_gate.py run [UV_RUN_ARGS...] -- COMMAND [ARGS...]
       uv_gate.py tool [--from SPEC] [UV_TOOL_RUN_ARGS...] -- EXECUTABLE [ARGS...]

prepare  uv sync --locked --offline; one online `uv sync --locked` only when
         uv proves a file is missing from the cache.
run      uv run --frozen --offline; never online, never retried.
tool     uv tool run --offline for a pinned tool; warmed online once on a
         proven cache miss.

Set UV_GATE_ALLOW_ONLINE=0 to forbid the online step entirely.
"""


def _split(arguments: cabc.Sequence[str]) -> tuple[list[str], list[str]]:
    """Split ``arguments`` at the first ``--``."""
    items = list(arguments)
    if "--" not in items:
        return items, []
    index = items.index("--")
    return items[:index], items[index + 1 :]


def main(
    argv: cabc.Sequence[str] | None = None,
    environ: cabc.Mapping[str, str] | None = None,
) -> int:
    """Run the helper and return its exit status.

    Parameters
    ----------
    argv:
        Arguments after the program name; defaults to ``sys.argv[1:]``.
    environ:
        The inherited environment; defaults to ``os.environ``.

    Returns
    -------
    int
        uv's exit status, or 2 when the helper itself refuses.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        sys.stderr.write(USAGE)
        return REFUSAL_STATUS if not args else 0
    subcommand, rest = args[0], args[1:]
    if subcommand not in {"prepare", "run", "tool"}:
        say(f"unknown subcommand {subcommand!r}")
        sys.stderr.write(USAGE)
        return REFUSAL_STATUS
    status, refused = _dispatch(subcommand, rest, environ)
    outcome = "refused" if refused else "ok" if status == 0 else "failed"
    say(f"metric uv-gate.{subcommand}={outcome}")
    return status


def _dispatch(
    subcommand: str,
    rest: cabc.Sequence[str],
    environ: cabc.Mapping[str, str] | None,
) -> tuple[int, bool]:
    """Run ``subcommand``; return its status and whether the helper refused."""
    context: Context | None = None
    try:
        context = build_context(os.environ if environ is None else environ, Path.cwd())
        if subcommand == "prepare":
            return prepare(context, rest), False
        before, after = _split(rest)
        if subcommand == "run":
            return run_gate(context, before, after), False
        return tool(context, before, after), False
    except GateError as exc:
        say(str(exc))
        return REFUSAL_STATUS, True
    finally:
        if context is not None:
            context.close()


if __name__ == "__main__":
    sys.exit(main())
