"""Normalize sccache's JSON statistics into the telemetry record's fields.

sccache prints different shapes across releases, but the fields this module
reads exist, unrenamed, in every release from 0.12 to 0.18: ``stats`` with
``compile_requests``, ``requests_not_cacheable``, ``non_cacheable_compilations``,
``cache_read_errors``, ``cache_write_errors`` and ``cache_timeouts`` (integers),
``cache_hits`` and ``cache_misses`` (per-language maps under ``counts``), plus
``version`` and ``cache_location``. Output that lacks one of them is refused
rather than read as zero.

A job whose server never started prints empty default statistics, which look
like a healthy cache that was never asked to do anything. The record therefore
carries an explicit ``sccache_status`` (``started``, ``fallback`` or
``absent``), and only a ``started`` job gets counts and ratios; the others
carry null, so a fallback never reads as healthy.

The module keeps three layers apart. ``normalize`` and ``decide_status`` are
pure. ``read_stats_file``, ``run_stats`` and ``server_reachable`` touch the
machine and return a result that says what happened (``Outcome``, a bool)
rather than hiding failure as ``None``. ``collect`` orchestrates them over the
injected ``Probes``.
"""

from __future__ import annotations

import dataclasses as dc
import json
import re
import shutil
import socket
import subprocess
import typing as typ
from pathlib import Path

RAW_LIMIT_BYTES = 2048
DEFAULT_PORT = 4226
SERVER_TIMEOUT_SECONDS = 2
STATS_TIMEOUT_SECONDS = 30
GIVEN_STATUSES = frozenset({"started", "fallback"})
LOCATION_KINDS = {
    "local disk": "local",
    "ghac": "ghac",
    "s3": "s3",
    "gcs": "gcs",
    "azure": "azure",
    "redis": "redis",
    "memcached": "memcached",
    "webdav": "webdav",
    "oss": "oss",
    "cos": "cos",
}
FIELDS = (
    "sccache_status",
    "sccache_stats_source",
    "sccache_stats_problem",
    "sccache_version",
    "sccache_cache_location_kind",
    "sccache_compile_requests",
    "sccache_hits",
    "sccache_misses",
    "sccache_hit_ratio",
    "sccache_cacheable_ratio",
    "sccache_read_errors",
    "sccache_write_errors",
    "sccache_timeouts",
    "sccache_non_cacheable",
    "sccache_requests_not_cacheable",
    "sccache_raw",
)
INTEGER_STATS = {
    "sccache_compile_requests": "compile_requests",
    "sccache_read_errors": "cache_read_errors",
    "sccache_write_errors": "cache_write_errors",
    "sccache_timeouts": "cache_timeouts",
    "sccache_non_cacheable": "non_cacheable_compilations",
    "sccache_requests_not_cacheable": "requests_not_cacheable",
}


class StatsError(ValueError):
    """The statistics lack a field this module needs, or it has the wrong type."""


def empty_fields(
    status: str, source: str | None = None, problem: str | None = None
) -> dict[str, typ.Any]:
    """Return every sccache field as null except status, source and problem."""
    return {
        **dict.fromkeys(FIELDS),
        "sccache_status": status,
        "sccache_stats_source": source,
        "sccache_stats_problem": problem,
    }


def location_kind(location: str) -> str:
    """Return the cache backend kind named by sccache's ``cache_location``."""
    head = re.split(r"[,:]", location, maxsplit=1)[0].strip().lower()
    return LOCATION_KINDS.get(head, "other")


def _count(stats: typ.Mapping[str, typ.Any], key: str) -> int:
    """Return an integer statistic, refusing a missing or non-integer one."""
    value = stats.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"stats.{key} is missing or not an integer"
        raise StatsError(message)
    return value


def _language_total(stats: typ.Mapping[str, typ.Any], key: str) -> int:
    """Return the sum of a per-language map's ``counts``."""
    section = stats.get(key)
    counts = section.get("counts") if isinstance(section, dict) else None
    if not isinstance(counts, dict):
        message = f"stats.{key}.counts is missing"
        raise StatsError(message)
    return sum(_count(counts, language) for language in counts)


def _text(document: typ.Mapping[str, typ.Any], key: str) -> str:
    """Return a required, non-empty string field of the document."""
    value = document.get(key)
    if not isinstance(value, str) or not value:
        message = f"{key} is missing or not a string"
        raise StatsError(message)
    return value


def _ratio(numerator: int, denominator: int) -> float | None:
    """Return the ratio rounded to four places, or None for an empty base."""
    return round(numerator / denominator, 4) if denominator else None


def _raw(document: typ.Mapping[str, typ.Any]) -> dict[str, typ.Any] | None:
    """Return the document when its compact JSON is under the size limit."""
    compact = json.dumps(document, separators=(",", ":"))
    return dict(document) if len(compact) < RAW_LIMIT_BYTES else None


def normalize(document: typ.Any) -> dict[str, typ.Any]:  # noqa: ANN401 - parsed JSON
    """Return the record fields for a parsed ``--stats-format=json`` document.

    Raises
    ------
    StatsError
        If the document is not an object with the fields sccache has printed
        since 0.12.
    """
    stats = document.get("stats") if isinstance(document, dict) else None
    if not isinstance(stats, dict):
        message = "no stats object in the sccache output"
        raise StatsError(message)
    fields = {name: _count(stats, key) for name, key in INTEGER_STATS.items()}
    hits = _language_total(stats, "cache_hits")
    misses = _language_total(stats, "cache_misses")
    version = _text(document, "version")
    location = _text(document, "cache_location")
    return {
        **fields,
        "sccache_version": version,
        "sccache_cache_location_kind": location_kind(location),
        "sccache_hits": hits,
        "sccache_misses": misses,
        "sccache_hit_ratio": _ratio(hits, hits + misses),
        "sccache_cacheable_ratio": _ratio(
            hits + misses, fields["sccache_compile_requests"]
        ),
        "sccache_raw": _raw(document),
    }


ABSENT = "absent"
OK = "ok"
UNREADABLE = "unreadable"
INVALID = "invalid"
FAILED = "failed"
TIMEOUT = "timeout"


@dc.dataclass(frozen=True)
class Outcome:
    """What reading one source of statistics came to."""

    kind: str
    document: typ.Any = None


def server_reachable(env: typ.Mapping[str, str]) -> bool:
    """Report whether an sccache server accepts connections.

    This is the one network-facing call, and it is a loopback connection only:
    a Unix socket when ``SCCACHE_SERVER_UDS`` names one, else the TCP port in
    ``SCCACHE_SERVER_PORT`` (default 4226).
    """
    uds = env.get("SCCACHE_SERVER_UDS") or ""
    try:
        if uds:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(SERVER_TIMEOUT_SECONDS)
                sock.connect(uds)
            return True
        with socket.create_connection(
            ("127.0.0.1", _port(env)), SERVER_TIMEOUT_SECONDS
        ):
            return True
    except (OSError, AttributeError):
        return False


def read_stats_file(path: str) -> Outcome:
    """Read the report file and say what happened.

    Returns
    -------
    Outcome
        ``absent`` for no path or no such file (the only case in which asking
        sccache instead is reasonable), ``unreadable`` for any other read
        failure, ``invalid`` for text that is not JSON, else ``ok``.
    """
    if not path:
        return Outcome(ABSENT)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Outcome(ABSENT)
    except (OSError, UnicodeDecodeError):
        return Outcome(UNREADABLE)
    try:
        return Outcome(OK, json.loads(text))
    except ValueError:
        return Outcome(INVALID)


def run_stats() -> Outcome:
    """Run ``sccache --show-stats --stats-format=json`` and say what happened.

    Returns
    -------
    Outcome
        ``timeout``, ``failed`` (no binary, or a nonzero exit), ``invalid``
        (output that is not JSON), else ``ok``.
    """
    command = [
        shutil.which("sccache") or "sccache",
        "--show-stats",
        "--stats-format=json",
    ]
    try:
        done = subprocess.run(  # noqa: S603,TID251 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=STATS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return Outcome(TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return Outcome(FAILED)
    try:
        return Outcome(OK, json.loads(done.stdout))
    except ValueError:
        return Outcome(INVALID)


@dc.dataclass(frozen=True)
class Probes:
    """The machine-facing calls, replaceable in tests."""

    which: typ.Callable[[str], str | None] = shutil.which
    reachable: typ.Callable[[typ.Mapping[str, str]], bool] = server_reachable
    read_file: typ.Callable[[str], Outcome] = read_stats_file
    run: typ.Callable[[], Outcome] = run_stats


def _port(env: typ.Mapping[str, str]) -> int:
    """Return the sccache server port from the environment, or the default."""
    try:
        return int(env.get("SCCACHE_SERVER_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


@dc.dataclass(frozen=True)
class Facts:
    """What the machine said about sccache, gathered before deciding."""

    binary_present: bool
    server_listening: bool


def gather_facts(env: typ.Mapping[str, str], probes: Probes) -> Facts:
    """Probe the machine for the facts ``decide_status`` needs.

    The server is probed only when there is a binary: with none, nothing can
    be listening that sccache owns.
    """
    present = probes.which("sccache") is not None
    return Facts(present, present and probes.reachable(env))


def decide_status(given: str, facts: Facts | None) -> str:
    """Return ``started``, ``fallback`` or ``absent``; pure.

    A ``started`` or ``fallback`` from ``setup-rust`` is used as it is. Without
    it: no binary is ``absent``; a binary with no server listening is
    ``fallback`` (the job runs without the cache); a listening server is
    ``started``. ``RUSTC_WRAPPER`` is not consulted, since sccache also serves
    C and C++ compilers as a launcher.
    """
    if given in GIVEN_STATUSES:
        return given
    if facts is None or not facts.binary_present:
        return "absent"
    return "started" if facts.server_listening else "fallback"


def _source(stats_file: str, probes: Probes) -> tuple[str, Outcome]:
    """Return ``(source, outcome)``: the report file, else sccache, once.

    Only an absent file falls through to sccache. A file that exists but cannot
    be read or parsed is a problem to report, not a reason to ask again.
    """
    from_file = probes.read_file(stats_file)
    if from_file.kind != ABSENT:
        return "file", from_file
    return "sccache", probes.run()


def _from_outcome(status: str, source: str, outcome: Outcome) -> dict[str, typ.Any]:
    """Return the record fields for one source's outcome."""
    if outcome.kind != OK:
        return empty_fields(status, UNREADABLE, f"{source}-{outcome.kind}")
    try:
        return {**empty_fields(status, source), **normalize(outcome.document)}
    except StatsError:
        return empty_fields(status, UNREADABLE, f"{source}-shape")


def collect(
    env: typ.Mapping[str, str],
    given_status: str,
    stats_file: str,
    probes: Probes | None = None,
) -> dict[str, typ.Any]:
    """Return the record's sccache fields; never raises.

    Only a ``started`` job gets counts. When statistics cannot be read or fail
    validation the status stays, ``sccache_stats_source`` says ``unreadable``
    and ``sccache_stats_problem`` names the source and what went wrong.
    """
    probes = probes or Probes()
    facts = None if given_status in GIVEN_STATUSES else gather_facts(env, probes)
    status = decide_status(given_status, facts)
    if status != "started":
        return empty_fields(status)
    source, outcome = _source(stats_file, probes)
    return _from_outcome(status, source, outcome)
