"""Normalizing sccache's JSON statistics into the telemetry record.

The fixtures are real sccache output: ``real-*`` files were captured from the
0.12.0, 0.16.0, 0.17.0 and 0.18.0 release binaries building a probe crate (one
cacheable compile twice, so one miss and one hit, plus incremental builds that
cannot be cached), ``real-0.17.0-no-server`` is what 0.17.0 prints when no
server is running, and ``ci-*`` files come from whitaker's Linux and Windows
coverage jobs on GitHub's cache. Expected figures are worked by hand from the
fixture's own numbers in the comments.
"""

from __future__ import annotations

import copy
import json
import os
import socket
import stat
import sys
import typing as typ

import pytest
from ci_telemetry_support import ACTION_DIR, FIXTURES, finish, sccache_stats

if typ.TYPE_CHECKING:
    from pathlib import Path

S = sccache_stats()
F = finish()
SCCACHE_FIXTURES = FIXTURES / "sccache"
FINISH_ACTION = ACTION_DIR / "action.yml"


def _load(name: str) -> dict[str, typ.Any]:
    return json.loads((SCCACHE_FIXTURES / name).read_text(encoding="utf-8"))


def _local(version: str) -> dict[str, typ.Any]:
    """Return the fields of a probe-crate capture: 1 hit, 1 miss, 17 requests."""
    return {
        "sccache_version": version,
        "sccache_cache_location_kind": "local",
        "sccache_compile_requests": 17,
        "sccache_hits": 1,
        "sccache_misses": 1,
        "sccache_hit_ratio": 0.5,  # 1 / (1 + 1)
        "sccache_cacheable_ratio": 0.1176,  # 2 / 17
        "sccache_read_errors": 0,
        "sccache_write_errors": 0,
        "sccache_timeouts": 0,
        "sccache_non_cacheable": 0,
        "sccache_requests_not_cacheable": 15,
    }


EXPECTED: dict[str, dict[str, typ.Any]] = {
    "real-0.12.0-local.json": _local("0.12.0"),
    "real-0.16.0-local.json": _local("0.16.0"),
    "real-0.17.0-local.json": _local("0.17.0"),
    "real-0.18.0-local.json": _local("0.18.0"),
    # Rust 705 + C/C++ 432 + Assembler 60 = 1197 hits; Rust misses 372;
    # 1197 / 1569 = 0.7629; 1569 / 2071 = 0.7576.
    "ci-0.17.0-ghac-linux.json": {
        "sccache_version": "0.17.0",
        "sccache_cache_location_kind": "ghac",
        "sccache_compile_requests": 2071,
        "sccache_hits": 1197,
        "sccache_misses": 372,
        "sccache_hit_ratio": 0.7629,
        "sccache_cacheable_ratio": 0.7576,
        "sccache_read_errors": 0,
        "sccache_write_errors": 0,
        "sccache_timeouts": 0,
        "sccache_non_cacheable": 0,
        "sccache_requests_not_cacheable": 486,
    },
    # 1481 hits, 195 misses: 1481 / 1676 = 0.8837; 1676 / 2307 = 0.7265.
    "ci-0.17.0-ghac-windows.json": {
        "sccache_version": "0.17.0",
        "sccache_cache_location_kind": "ghac",
        "sccache_compile_requests": 2307,
        "sccache_hits": 1481,
        "sccache_misses": 195,
        "sccache_hit_ratio": 0.8837,
        "sccache_cacheable_ratio": 0.7265,
        "sccache_read_errors": 0,
        "sccache_write_errors": 0,
        "sccache_timeouts": 0,
        "sccache_non_cacheable": 0,
        "sccache_requests_not_cacheable": 622,
    },
    # No requests at all: counts are real zeros but there is nothing to divide.
    "real-0.17.0-no-server.json": {
        "sccache_version": "0.17.0",
        "sccache_cache_location_kind": "local",
        "sccache_compile_requests": 0,
        "sccache_hits": 0,
        "sccache_misses": 0,
        "sccache_hit_ratio": None,
        "sccache_cacheable_ratio": None,
        "sccache_read_errors": 0,
        "sccache_write_errors": 0,
        "sccache_timeouts": 0,
        "sccache_non_cacheable": 0,
        "sccache_requests_not_cacheable": 0,
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_real_outputs_normalize_to_the_same_fields(name: str) -> None:
    """Four releases and two backends give the same field set and exact figures."""
    fields = S.normalize(_load(name))
    raw = fields.pop("sccache_raw")
    assert fields == EXPECTED[name]
    assert raw == _load(name)


def test_every_release_has_the_same_field_names() -> None:
    """The field set does not depend on the release that printed the statistics."""
    names = {tuple(sorted(S.normalize(_load(n)))) for n in EXPECTED}
    assert len(names) == 1
    assert set(names.pop()) == set(S.FIELDS) - {
        "sccache_status",
        "sccache_stats_source",
    }


def test_a_large_document_drops_the_raw_copy_but_keeps_the_figures() -> None:
    """Raw JSON is kept only under 2 KB of compact JSON."""
    document = _load("real-0.17.0-local.json")
    document["stats"]["not_cached"] = {f"reason {i}": i for i in range(200)}
    fields = S.normalize(document)
    assert fields["sccache_raw"] is None
    assert fields["sccache_hits"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("stats"),
        lambda d: d["stats"].pop("compile_requests"),
        lambda d: d["stats"].update(compile_requests="17"),
        lambda d: d["stats"].update(cache_timeouts=True),
        lambda d: d["stats"].pop("cache_hits"),
        lambda d: d["stats"].update(cache_misses=3),
        lambda d: d["stats"]["cache_hits"].update(counts={"Rust": "1"}),
        lambda d: d["stats"].pop("requests_not_cacheable"),
    ],
    ids=[
        "no-stats",
        "no-requests",
        "string-requests",
        "bool-timeouts",
        "no-hit-map",
        "misses-not-a-map",
        "string-hit-count",
        "no-not-cacheable",
    ],
)
def test_output_missing_a_field_is_refused_not_read_as_zero(
    mutate: typ.Callable[[dict[str, typ.Any]], object],
) -> None:
    """A shape change must surface, not become a healthy-looking zero."""
    document = copy.deepcopy(_load("real-0.18.0-local.json"))
    mutate(document)
    with pytest.raises(S.StatsError):
        S.normalize(document)


def test_a_non_object_document_is_refused() -> None:
    """JSON that is not an object has no stats."""
    with pytest.raises(S.StatsError):
        S.normalize([1, 2])


@pytest.mark.parametrize(
    ("location", "kind"),
    [
        ('Local disk: "/x"', "local"),
        ("ghac, name: sccache-v0.17.0, prefix: /sccache/", "ghac"),
        ("s3, name: b", "s3"),
        ("Redis: tcp://x", "redis"),
        ("webdav, endpoint: x", "webdav"),
        ("something new", "other"),
        ("", "other"),
    ],
)
def test_cache_location_kinds(location: str, kind: str) -> None:
    """The backend is named by the text before the first comma or colon."""
    assert S.location_kind(location) == kind


# --- status and the three ways to get statistics --------------------------


def _probes(
    *,
    binary: bool = True,
    listening: bool = True,
    file_doc: object = None,
    run_doc: object = None,
) -> tuple[typ.Any, list[str]]:
    calls: list[str] = []

    def which(_name: str) -> str | None:
        return "/usr/bin/sccache" if binary else None

    def reachable(port: int) -> bool:
        calls.append(f"reachable:{port}")
        return listening

    def read_file(path: str) -> object:
        calls.append(f"read:{path}")
        return file_doc

    def run() -> object:
        calls.append("run")
        return run_doc

    return S.Probes(which, reachable, read_file, run), calls


WRAPPED = {"RUSTC_WRAPPER": "sccache"}
GOOD = _load("real-0.18.0-local.json")


def test_started_reads_the_report_file_first() -> None:
    """With sccache-report's file present, sccache is not asked again."""
    probes, calls = _probes(file_doc=GOOD, run_doc={"never": "used"})
    fields = S.collect(WRAPPED, "started", "sccache-stats.json", probes)
    assert fields["sccache_status"] == "started"
    assert fields["sccache_stats_source"] == "file"
    assert fields["sccache_hits"] == 1
    assert calls == ["read:sccache-stats.json"]


def test_started_without_the_file_asks_sccache_once() -> None:
    """The fallback path: no file, so run sccache --show-stats."""
    probes, calls = _probes(file_doc=None, run_doc=GOOD)
    fields = S.collect(WRAPPED, "started", "missing.json", probes)
    assert fields["sccache_stats_source"] == "sccache"
    assert fields["sccache_misses"] == 1
    assert calls == ["read:missing.json", "run"]


def test_started_with_no_readable_statistics_says_unreadable() -> None:
    """Neither source works: the status stays, the counts are null, the gap shows."""
    probes, _ = _probes()
    fields = S.collect(WRAPPED, "started", "x.json", probes)
    assert fields["sccache_status"] == "started"
    assert fields["sccache_stats_source"] == "unreadable"
    assert fields["sccache_hits"] is None
    assert fields["sccache_hit_ratio"] is None


def test_invalid_statistics_are_unreadable_not_zero() -> None:
    """A shape that fails validation gives null counts, never a healthy zero."""
    probes, _ = _probes(file_doc={"stats": {}})
    fields = S.collect(WRAPPED, "started", "x.json", probes)
    assert fields["sccache_stats_source"] == "unreadable"
    assert fields["sccache_compile_requests"] is None


def test_a_fallback_with_zero_statistics_never_reads_as_healthy() -> None:
    """setup-rust said fallback; even a file of zeros is not consulted."""
    zeros = _load("real-0.17.0-no-server.json")
    probes, calls = _probes(file_doc=zeros, run_doc=zeros)
    fields = S.collect(WRAPPED, "fallback", "sccache-stats.json", probes)
    assert fields["sccache_status"] == "fallback"
    assert all(fields[name] is None for name in S.FIELDS if name != "sccache_status")
    assert calls == []


@pytest.mark.parametrize(
    ("binary", "wrapper", "listening", "expected"),
    [
        (False, "sccache", True, "absent"),
        (True, "", True, "fallback"),
        (True, "   ", True, "fallback"),
        (True, "sccache", False, "fallback"),
        (True, "sccache", True, "started"),
    ],
)
def test_status_is_inferred_when_setup_rust_did_not_say(
    *, binary: bool, wrapper: str, listening: bool, expected: str
) -> None:
    """absent: no binary; fallback: empty wrapper or no server; else started."""
    probes, _ = _probes(binary=binary, listening=listening, file_doc=GOOD, run_doc=GOOD)
    fields = S.collect({"RUSTC_WRAPPER": wrapper}, "", "x.json", probes)
    assert fields["sccache_status"] == expected
    assert (fields["sccache_hits"] is not None) == (expected == "started")


def test_an_unrecognized_given_status_is_inferred_instead() -> None:
    """Only started and fallback are taken from setup-rust."""
    probes, _ = _probes(binary=False)
    assert S.collect(WRAPPED, "weird", "", probes)["sccache_status"] == "absent"


@pytest.mark.parametrize(
    ("env", "port"),
    [
        ({}, 4226),
        ({"SCCACHE_SERVER_PORT": "4999"}, 4999),
        ({"SCCACHE_SERVER_PORT": "x"}, 4226),
    ],
)
def test_the_server_probe_uses_sccaches_port(env: dict[str, str], port: int) -> None:
    """The configured port is probed, with a bad value giving the default."""
    probes, calls = _probes(listening=True, file_doc=GOOD)
    S.collect({**env, **WRAPPED}, "", "x.json", probes)
    assert f"reachable:{port}" in calls


def test_server_reachable_sees_a_listening_socket() -> None:
    """A listening port is reachable and a closed one is not."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert S.server_reachable(port) is True
    assert S.server_reachable(port) is False


def test_read_stats_file_returns_none_for_missing_and_junk(tmp_path: Path) -> None:
    """Absent, empty-path and non-JSON files are all just 'no file'."""
    junk = tmp_path / "junk.json"
    junk.write_text("{nope", encoding="utf-8")
    assert S.read_stats_file("") is None
    assert S.read_stats_file(str(tmp_path / "absent.json")) is None
    assert S.read_stats_file(str(junk)) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD), encoding="utf-8")
    assert S.read_stats_file(str(good)) == GOOD


@pytest.mark.skipif(sys.platform == "win32", reason="stub is a POSIX script")
class TestRunStats:
    """``run_stats`` calls the sccache on PATH, and survives its failures."""

    def _stub(self, tmp_path: Path, body: str) -> Path:
        stub = tmp_path / "sccache"
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return tmp_path

    def test_parses_the_json_it_prints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stub echoes its arguments to a file and prints a fixture."""
        fixture = SCCACHE_FIXTURES / "real-0.18.0-local.json"
        bin_dir = self._stub(
            tmp_path, f'echo "$@" > "{tmp_path}/args"; cat "{fixture}"'
        )
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        assert S.run_stats() == GOOD
        assert (
            tmp_path / "args"
        ).read_text().strip() == "--show-stats --stats-format=json"

    @pytest.mark.parametrize(
        "body", ["exit 3", "echo not json", "echo '[1]' >&2; exit 0"]
    )
    def test_failures_give_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
    ) -> None:
        """A failing or junk-printing sccache is 'no statistics'."""
        monkeypatch.setenv("PATH", str(self._stub(tmp_path, body)))
        assert S.run_stats() is None


# --- through the finish script -------------------------------------------


def test_finish_adds_the_sccache_fields_to_every_record(tmp_path: Path) -> None:
    """The record always carries the full sccache field set."""
    env = {"CT_SCCACHE_STATUS": "fallback"}
    record = F.finish(tmp_path / "ci-telemetry", env)
    assert set(S.FIELDS) <= set(record)
    assert record["sccache_status"] == "fallback"
    assert record["sampler"] == "not-started"


def test_finish_reads_the_report_file_named_by_its_inputs(tmp_path: Path) -> None:
    """CT_SCCACHE_STATS_FILE is the sccache-stats-file input."""
    path = SCCACHE_FIXTURES / "ci-0.17.0-ghac-linux.json"
    env = {
        "CT_SCCACHE_STATUS": "started",
        "CT_SCCACHE_STATS_FILE": str(path),
        "RUSTC_WRAPPER": "sccache",
    }
    record = F.finish(tmp_path / "ci-telemetry", env)
    assert record["sccache_hits"] == 1197
    assert record["sccache_stats_source"] == "file"


def test_the_manifest_declares_and_forwards_the_sccache_inputs() -> None:
    """Both inputs are optional, with setup-rust's and sccache-report's defaults."""
    import yaml

    manifest = yaml.safe_load(FINISH_ACTION.read_text(encoding="utf-8"))
    inputs = manifest["inputs"]
    assert {k: (v["required"], v["default"]) for k, v in inputs.items()} == {
        "sccache-status": (False, ""),
        "sccache-stats-file": (False, "sccache-stats.json"),
    }
    (step,) = manifest["runs"]["steps"]
    assert step["env"]["CT_SCCACHE_STATUS"] == "${{ inputs.sccache-status }}"
    assert step["env"]["CT_SCCACHE_STATS_FILE"] == "${{ inputs.sccache-stats-file }}"
