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


def empty_fields(status: str, source: str | None = None) -> dict[str, typ.Any]:
    """Return every sccache field as null except the status and source."""
    return {
        **dict.fromkeys(FIELDS),
        "sccache_status": status,
        "sccache_stats_source": source,
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
    location = document.get("cache_location")
    return {
        **fields,
        "sccache_version": str(document.get("version") or "") or None,
        "sccache_cache_location_kind": location_kind(str(location or "")),
        "sccache_hits": hits,
        "sccache_misses": misses,
        "sccache_hit_ratio": _ratio(hits, hits + misses),
        "sccache_cacheable_ratio": _ratio(
            hits + misses, fields["sccache_compile_requests"]
        ),
        "sccache_raw": _raw(document),
    }


def server_reachable(port: int) -> bool:
    """Report whether something accepts connections on the sccache port."""
    try:
        with socket.create_connection(("127.0.0.1", port), SERVER_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def read_stats_file(path: str) -> typ.Any:  # noqa: ANN401 - parsed JSON
    """Return the parsed JSON in *path*, or None if it is absent or not JSON."""
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_stats() -> typ.Any:  # noqa: ANN401 - parsed JSON
    """Run ``sccache --show-stats --stats-format=json``; None on any failure."""
    try:
        done = subprocess.run(  # noqa: S603,TID251 - fixed argv, no shell
            [
                shutil.which("sccache") or "sccache",
                "--show-stats",
                "--stats-format=json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=STATS_TIMEOUT_SECONDS,
        )
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@dc.dataclass(frozen=True)
class Probes:
    """The machine-facing calls, replaceable in tests."""

    which: typ.Callable[[str], str | None] = shutil.which
    reachable: typ.Callable[[int], bool] = server_reachable
    read_file: typ.Callable[[str], typ.Any] = read_stats_file
    run: typ.Callable[[], typ.Any] = run_stats


def _port(env: typ.Mapping[str, str]) -> int:
    """Return the sccache server port from the environment, or the default."""
    try:
        return int(env.get("SCCACHE_SERVER_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def infer_status(given: str, env: typ.Mapping[str, str], probes: Probes) -> str:
    """Return ``started``, ``fallback`` or ``absent`` for this job.

    A status from ``setup-rust`` is used as it is. Otherwise: no ``sccache``
    binary is ``absent``; an empty ``RUSTC_WRAPPER``, or a server nobody is
    listening to, is ``fallback``; anything else is ``started``.
    """
    if given in GIVEN_STATUSES:
        return given
    if probes.which("sccache") is None:
        return "absent"
    if not (env.get("RUSTC_WRAPPER") or "").strip():
        return "fallback"
    return "started" if probes.reachable(_port(env)) else "fallback"


def _document(stats_file: str, probes: Probes) -> tuple[str | None, typ.Any]:
    """Return ``(source, document)``: the report's file first, else sccache."""
    document = probes.read_file(stats_file)
    if document is not None:
        return "file", document
    document = probes.run()
    return ("sccache", document) if document is not None else (None, None)


def collect(
    env: typ.Mapping[str, str],
    given_status: str,
    stats_file: str,
    probes: Probes | None = None,
) -> dict[str, typ.Any]:
    """Return the record's sccache fields; never raises.

    Only a ``started`` job gets counts. When statistics cannot be read, or fail
    validation, the status stays and ``sccache_stats_source`` says
    ``unreadable`` so the gap is visible.
    """
    probes = probes or Probes()
    status = infer_status(given_status, env, probes)
    if status != "started":
        return empty_fields(status)
    source, document = _document(stats_file, probes)
    if document is None:
        return empty_fields(status, "unreadable")
    try:
        return {**empty_fields(status, source), **normalize(document)}
    except StatsError:
        return empty_fields(status, "unreadable")
