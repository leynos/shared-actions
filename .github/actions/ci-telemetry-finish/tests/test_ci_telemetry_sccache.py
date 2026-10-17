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
from hypothesis import given, settings
from hypothesis import strategies as st

if typ.TYPE_CHECKING:
    from pathlib import Path

LANGUAGES = ["Rust", "C/C++", "Assembler"]
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
        "sccache_stats_problem",
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
        lambda d: d.pop("version"),
        lambda d: d.update(version=18),
        lambda d: d.pop("cache_location"),
        lambda d: d.update(cache_location=""),
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
        "no-version",
        "numeric-version",
        "no-location",
        "empty-location",
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


@settings(max_examples=100, deadline=None)
@given(
    hits=st.dictionaries(st.sampled_from(LANGUAGES), st.integers(0, 10**6)),
    misses=st.dictionaries(st.sampled_from(LANGUAGES), st.integers(0, 10**6)),
    requests=st.integers(0, 10**7),
)
def test_normalized_totals_and_ratios_follow_the_maps(
    hits: dict[str, int], misses: dict[str, int], requests: int
) -> None:
    """Sums equal the map sums; ratios use their denominators or are null."""
    document = copy.deepcopy(_load("real-0.18.0-local.json"))
    document["stats"]["cache_hits"] = {"counts": hits}
    document["stats"]["cache_misses"] = {"counts": misses}
    document["stats"]["compile_requests"] = requests
    fields = S.normalize(document)
    total_hits, total_misses = sum(hits.values()), sum(misses.values())
    assert fields["sccache_hits"] == total_hits
    assert fields["sccache_misses"] == total_misses
    attempts = total_hits + total_misses
    if attempts:
        assert fields["sccache_hit_ratio"] == round(total_hits / attempts, 4)
        assert 0.0 <= fields["sccache_hit_ratio"] <= 1.0
    else:
        assert fields["sccache_hit_ratio"] is None
    if requests:
        assert fields["sccache_cacheable_ratio"] == round(attempts / requests, 4)
    else:
        assert fields["sccache_cacheable_ratio"] is None


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


# --- status and the ways to get statistics -------------------------------


def _probes(
    *,
    binary: bool = True,
    listening: bool = True,
    file: typ.Any = None,  # noqa: ANN401 - an Outcome
    run: typ.Any = None,  # noqa: ANN401
) -> tuple[typ.Any, list[str]]:
    """Return injected probes and the log of what they were asked."""
    calls: list[str] = []

    def which(_name: str) -> str | None:
        return "/usr/bin/sccache" if binary else None

    def reachable(env: typ.Mapping[str, str]) -> bool:
        calls.append(f"reachable:{env.get('SCCACHE_SERVER_PORT', '')}")
        return listening

    def read_file(path: str) -> typ.Any:  # noqa: ANN401
        calls.append(f"read:{path}")
        return file or S.Outcome(S.ABSENT)

    def run_stats() -> typ.Any:  # noqa: ANN401
        calls.append("run")
        return run or S.Outcome(S.FAILED)

    return S.Probes(which, reachable, read_file, run_stats), calls


def _ok(document: object) -> typ.Any:  # noqa: ANN401
    return S.Outcome(S.OK, document)


GOOD = _load("real-0.18.0-local.json")


def test_started_reads_the_report_file_first() -> None:
    """With sccache-report's file present, sccache is not asked again."""
    probes, calls = _probes(file=_ok(GOOD), run=_ok({"never": "used"}))
    fields = S.collect({}, "started", "sccache-stats.json", probes)
    assert fields["sccache_status"] == "started"
    assert fields["sccache_stats_source"] == "file"
    assert fields["sccache_stats_problem"] is None
    assert fields["sccache_hits"] == 1
    assert calls == ["read:sccache-stats.json"]


def test_started_without_the_file_asks_sccache_once() -> None:
    """The fallback path: no file, so run sccache --show-stats."""
    probes, calls = _probes(run=_ok(GOOD))
    fields = S.collect({}, "started", "missing.json", probes)
    assert fields["sccache_stats_source"] == "sccache"
    assert fields["sccache_misses"] == 1
    assert calls == ["read:missing.json", "run"]


@pytest.mark.parametrize("kind", [S.UNREADABLE, S.INVALID])
def test_a_present_but_bad_report_file_is_not_replaced_by_a_rerun(kind: str) -> None:
    """Only an absent file falls through; a bad one is reported as a problem."""
    probes, calls = _probes(file=S.Outcome(kind), run=_ok(GOOD))
    fields = S.collect({}, "started", "x.json", probes)
    assert fields["sccache_stats_source"] == "unreadable"
    assert fields["sccache_stats_problem"] == f"file-{kind}"
    assert fields["sccache_hits"] is None
    assert calls == ["read:x.json"]


@pytest.mark.parametrize("kind", [S.FAILED, S.TIMEOUT, S.INVALID])
def test_no_file_and_a_failing_sccache_names_the_problem(kind: str) -> None:
    """Neither source works: counts are null and the problem says why."""
    probes, _ = _probes(run=S.Outcome(kind))
    fields = S.collect({}, "started", "x.json", probes)
    assert fields["sccache_status"] == "started"
    assert fields["sccache_stats_source"] == "unreadable"
    assert fields["sccache_stats_problem"] == f"sccache-{kind}"
    assert fields["sccache_hit_ratio"] is None


def test_statistics_of_the_wrong_shape_are_a_shape_problem_not_zero() -> None:
    """A document failing validation gives null counts and a shape problem."""
    probes, _ = _probes(file=_ok({"stats": {}}))
    fields = S.collect({}, "started", "x.json", probes)
    assert fields["sccache_stats_source"] == "unreadable"
    assert fields["sccache_stats_problem"] == "file-shape"
    assert fields["sccache_compile_requests"] is None


def test_a_fallback_with_zero_statistics_never_reads_as_healthy() -> None:
    """setup-rust said fallback; even a file of zeros is not consulted."""
    zeros = _ok(_load("real-0.17.0-no-server.json"))
    probes, calls = _probes(file=zeros, run=zeros)
    fields = S.collect({}, "fallback", "sccache-stats.json", probes)
    assert fields["sccache_status"] == "fallback"
    assert all(fields[n] is None for n in S.FIELDS if n != "sccache_status")
    assert calls == []


@pytest.mark.parametrize(
    ("binary", "listening", "expected"),
    [(False, True, "absent"), (True, False, "fallback"), (True, True, "started")],
)
def test_status_is_inferred_when_setup_rust_did_not_say(
    *, binary: bool, listening: bool, expected: str
) -> None:
    """absent: no binary; fallback: nobody listening; else started."""
    probes, _ = _probes(
        binary=binary, listening=listening, file=_ok(GOOD), run=_ok(GOOD)
    )
    fields = S.collect({}, "", "x.json", probes)
    assert fields["sccache_status"] == expected
    assert (fields["sccache_hits"] is not None) == (expected == "started")


def test_an_empty_rustc_wrapper_does_not_mean_fallback() -> None:
    """Sccache also serves C and C++ compilers as a launcher; the server decides."""
    probes, _ = _probes(listening=True, file=_ok(GOOD))
    fields = S.collect({"RUSTC_WRAPPER": ""}, "", "x.json", probes)
    assert fields["sccache_status"] == "started"


def test_an_unrecognized_given_status_is_inferred_instead() -> None:
    """Only started and fallback are taken from setup-rust."""
    probes, _ = _probes(binary=False)
    assert S.collect({}, "weird", "", probes)["sccache_status"] == "absent"


def test_a_given_status_probes_nothing() -> None:
    """With setup-rust's word, neither the binary nor the server is asked about."""
    probes, calls = _probes(file=_ok(GOOD))
    S.collect({}, "started", "x.json", probes)
    assert not any(c.startswith("reachable") for c in calls)


def test_decide_status_is_pure() -> None:
    """The status rule needs only the given status and the gathered facts."""
    facts = S.Facts(binary_present=True, server_listening=False)
    assert S.decide_status("", facts) == "fallback"
    assert S.decide_status("", None) == "absent"
    assert S.decide_status("started", None) == "started"


@pytest.mark.parametrize(
    ("env", "port"),
    [
        ({}, 4226),
        ({"SCCACHE_SERVER_PORT": "4999"}, 4999),
        ({"SCCACHE_SERVER_PORT": "x"}, 4226),
    ],
)
def test_the_server_probe_uses_sccaches_port(
    env: dict[str, str], port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured port is probed, with a bad value giving the default."""
    seen: list[int] = []

    def refuse(address: tuple[str, int], _timeout: float) -> typ.Any:  # noqa: ANN401
        seen.append(address[1])
        raise OSError

    monkeypatch.setattr(S.socket, "create_connection", refuse)
    assert S.server_reachable(env) is False
    assert seen == [port]


def test_server_reachable_sees_a_listening_socket() -> None:
    """A listening port is reachable and a closed one is not."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        env = {"SCCACHE_SERVER_PORT": str(server.getsockname()[1])}
        assert S.server_reachable(env) is True
    assert S.server_reachable(env) is False


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_server_reachable_sees_a_unix_socket(tmp_path: Path) -> None:
    """SCCACHE_SERVER_UDS is probed instead of the TCP port when set."""
    path = tmp_path / "s"
    env = {"SCCACHE_SERVER_UDS": str(path), "SCCACHE_SERVER_PORT": "1"}
    assert S.server_reachable(env) is False
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen(1)
        assert S.server_reachable(env) is True


def test_read_stats_file_tells_absent_unreadable_and_invalid_apart(
    tmp_path: Path,
) -> None:
    """Each way a report file can fail has its own outcome."""
    junk = tmp_path / "junk.json"
    junk.write_text("{nope", encoding="utf-8")
    (tmp_path / "dir").mkdir()
    assert S.read_stats_file("").kind == S.ABSENT
    assert S.read_stats_file(str(tmp_path / "absent.json")).kind == S.ABSENT
    assert S.read_stats_file(str(junk)).kind == S.INVALID
    assert S.read_stats_file(str(tmp_path / "dir")).kind == S.UNREADABLE
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD), encoding="utf-8")
    assert S.read_stats_file(str(good)) == S.Outcome(S.OK, GOOD)


@pytest.mark.skipif(sys.platform == "win32", reason="stub is a POSIX script")
class TestRunStats:
    """``run_stats`` calls the sccache on PATH, and says how it failed."""

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
        body = f'echo "$@" > "{tmp_path}/args"; cat "{fixture}"'
        bin_dir = self._stub(tmp_path, body)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        assert S.run_stats() == S.Outcome(S.OK, GOOD)
        args = (tmp_path / "args").read_text().strip()
        assert args == "--show-stats --stats-format=json"

    @pytest.mark.parametrize(
        ("body", "kind"),
        [("exit 3", S.FAILED), ("echo not json", S.INVALID), ("sleep 5", S.TIMEOUT)],
    )
    def test_failures_say_what_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        body: str,
        kind: str,
    ) -> None:
        """A failing, junk-printing or hanging sccache has its own outcome."""
        stub_dir = self._stub(tmp_path, body)
        monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}/usr/bin:/bin")
        monkeypatch.setattr(S, "STATS_TIMEOUT_SECONDS", 1)
        assert S.run_stats().kind == kind

    def test_no_binary_is_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no sccache on PATH the command cannot run."""
        monkeypatch.setenv("PATH", str(tmp_path))
        assert S.run_stats().kind == S.FAILED


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
