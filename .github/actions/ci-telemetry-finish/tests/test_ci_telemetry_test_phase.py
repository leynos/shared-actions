"""The test-phase sccache figures and the threshold breaches.

The fields are additive to schema 1, so the tests hold two things: the figures
and warnings are right when a post-build snapshot exists, and every new field
is null (and no old field changes) when it does not.
"""

from __future__ import annotations

import copy
import json
import typing as typ

import pytest
from ci_telemetry_support import FIXTURES, SCRIPTS, finish, load

if typ.TYPE_CHECKING:
    from pathlib import Path

T = load("ct_test_phase", SCRIPTS / "test_phase.py")
F = finish()
BASE = json.loads((FIXTURES / "sccache" / "real-0.17.0-local.json").read_text())
MAIN_PUSH = {
    "CT_THRESHOLDS_MODE": "main-push",
    "CT_DEFAULT_BRANCH": "main",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/main",
}


def snapshot(hits: int, misses: int, requests: int) -> dict[str, typ.Any]:
    """Return the real fixture with its counters replaced."""
    doc = copy.deepcopy(BASE)
    doc["stats"]["cache_hits"]["counts"] = {"Rust": hits}
    doc["stats"]["cache_misses"]["counts"] = {"Rust": misses}
    doc["stats"]["compile_requests"] = requests
    return doc


def write(tmp_path: Path, name: str, doc: str | dict[str, typ.Any]) -> str:
    """Write *doc* (text, or an object as JSON) under tmp_path; return the path."""
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return str(path)


def run(
    tmp_path: Path,
    build: str | dict[str, typ.Any] | None,
    final: dict[str, typ.Any],
    extra: dict[str, str] | None = None,
) -> dict[str, typ.Any]:
    """Return the record ``finish`` builds from the two snapshot files."""
    env = {
        "CT_SCCACHE_STATUS": "started",
        "CT_SCCACHE_STATS_FILE": write(tmp_path, "final.json", final),
        "RUNNER_TEMP": str(tmp_path),
        **(extra or {}),
    }
    if build is not None:
        env["CT_SCCACHE_BUILD_STATS_FILE"] = write(tmp_path, "build.json", build)
    return F.finish(tmp_path / "state", env)


def test_delta_subtracts_the_build_snapshot() -> None:
    """Hits, misses and requests are the final minus the build counters."""
    final = {
        "sccache_hits": 900,
        "sccache_misses": 100,
        "sccache_compile_requests": 1200,
    }
    build = {
        "sccache_hits": 700,
        "sccache_misses": 100,
        "sccache_compile_requests": 900,
    }
    fields = T.delta(final, build)
    assert fields["sccache_test_hits"] == 200
    assert fields["sccache_test_misses"] == 0
    assert fields["sccache_test_compile_requests"] == 300
    assert fields["sccache_test_hit_ratio"] == 1.0
    assert fields["sccache_test_problem"] is None


def test_an_empty_phase_has_no_ratio() -> None:
    """No attempts in the phase gives a null ratio, not zero or a division."""
    same = {"sccache_hits": 5, "sccache_misses": 5, "sccache_compile_requests": 12}
    assert T.delta(same, same)["sccache_test_hit_ratio"] is None


def test_a_counter_reset_is_reported_not_subtracted() -> None:
    """A server restart between snapshots makes the difference meaningless."""
    final = {"sccache_hits": 1, "sccache_misses": 0, "sccache_compile_requests": 1}
    build = {"sccache_hits": 9, "sccache_misses": 0, "sccache_compile_requests": 9}
    fields = T.delta(final, build)
    assert fields["sccache_test_problem"] == "counter-reset"
    assert fields["sccache_test_hits"] is None


def test_without_a_snapshot_every_new_field_is_null(tmp_path: Path) -> None:
    """No build snapshot: the six fields are present and null, old fields intact."""
    record = run(tmp_path, None, snapshot(10, 2, 20))
    assert all(record[name] is None for name in T.FIELDS)
    assert record["sccache_hits"] == 10
    assert record["sccache_hit_ratio"] == round(10 / 12, 4)


def test_old_fields_are_identical_with_and_without_a_snapshot(tmp_path: Path) -> None:
    """Supplying a snapshot adds the new fields and changes no existing one."""
    without = run(tmp_path, None, snapshot(10, 2, 20))
    with_snapshot = run(tmp_path, snapshot(4, 2, 8), snapshot(10, 2, 20))
    old = {k: v for k, v in without.items() if k not in T.FIELDS}
    assert {k: v for k, v in with_snapshot.items() if k not in T.FIELDS} == old
    assert set(with_snapshot) == set(without)


def test_the_phase_is_measured_from_the_files(tmp_path: Path) -> None:
    """End to end: 6 hits and 0 misses in the phase give ratio 1.0."""
    record = run(tmp_path, snapshot(4, 2, 8), snapshot(10, 2, 20))
    assert record["sccache_test_hits"] == 6
    assert record["sccache_test_misses"] == 0
    assert record["sccache_test_compile_requests"] == 12


@pytest.mark.parametrize(
    ("content", "problem"),
    [("not json", "snapshot-invalid"), ('{"stats": 1}', "snapshot-shape")],
)
def test_an_unusable_snapshot_names_its_problem(
    tmp_path: Path, content: str, problem: str
) -> None:
    """A snapshot that exists but cannot be used is reported, never ignored."""
    record = run(tmp_path, content, snapshot(10, 2, 20))
    assert record["sccache_test_problem"] == problem
    assert record["sccache_test_hit_ratio"] is None


def test_a_fallback_job_gets_no_test_phase(tmp_path: Path) -> None:
    """Without a started server there are no counts to subtract."""
    record = run(
        tmp_path,
        snapshot(1, 1, 2),
        snapshot(2, 2, 4),
        {"CT_SCCACHE_STATUS": "fallback"},
    )
    assert all(record[name] is None for name in T.FIELDS)


def test_a_cold_test_phase_on_a_main_push_breaches(tmp_path: Path) -> None:
    """A 5% test-phase hit rate over 100 requests is a hit-ratio breach."""
    record = run(tmp_path, snapshot(0, 0, 0), snapshot(5, 95, 100), MAIN_PUSH)
    assert record["sccache_test_hit_ratio"] == 0.05
    assert record["threshold_breaches"] == "test_hit_ratio"


def test_a_warm_phase_does_not_breach(tmp_path: Path) -> None:
    """A 99% hit rate passes and says `none`."""
    record = run(tmp_path, snapshot(0, 0, 0), snapshot(99, 1, 100), MAIN_PUSH)
    assert record["threshold_breaches"] == "none"


def test_a_small_phase_is_not_judged() -> None:
    """Below the minimum request count the ratio is noise and is not judged."""
    record = {"sccache_test_hit_ratio": 0.0, "sccache_test_compile_requests": 19}
    assert T.breaches(record, MAIN_PUSH) == []


def test_low_free_disk_breaches() -> None:
    """2 GiB free is below the 3 GiB default."""
    assert T.breaches({"min_free_disk_mib": 2048}, MAIN_PUSH) == ["min_free_disk"]
    assert T.breaches({"min_free_disk_mib": 5474}, MAIN_PUSH) == []
    assert T.breaches({"min_free_disk_mib": None}, MAIN_PUSH) == []


def test_thresholds_are_inputs() -> None:
    """A caller's numbers replace the defaults; a bad number falls back."""
    env = {**MAIN_PUSH, "CT_MIN_FREE_DISK_MIB": "9000"}
    assert T.breaches({"min_free_disk_mib": 5474}, env) == ["min_free_disk"]
    bad = {**MAIN_PUSH, "CT_MIN_FREE_DISK_MIB": "lots"}
    assert T.breaches({"min_free_disk_mib": 2048}, bad) == ["min_free_disk"]


@pytest.mark.parametrize(
    "env",
    [
        {**MAIN_PUSH, "GITHUB_EVENT_NAME": "pull_request"},
        {**MAIN_PUSH, "GITHUB_REF": "refs/heads/topic"},
        {**MAIN_PUSH, "CT_DEFAULT_BRANCH": ""},
        {**MAIN_PUSH, "CT_THRESHOLDS_MODE": "never"},
        {},
    ],
)
def test_thresholds_do_not_apply_off_the_default_branch(env: dict[str, str]) -> None:
    """Only a push to the default branch is judged (mode main-push)."""
    assert T.judge({"min_free_disk_mib": 1}, env) == {"threshold_breaches": None}


def test_always_mode_judges_everywhere() -> None:
    """`always` ignores the event and the branch."""
    env = {"CT_THRESHOLDS_MODE": "always"}
    assert T.judge({"min_free_disk_mib": 1}, env) == {
        "threshold_breaches": "min_free_disk"
    }


def test_a_breach_prints_a_warning_and_the_job_does_not_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker is followed by `::warning::` lines and main() still returns 0."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    for key, value in {
        **MAIN_PUSH,
        "CT_SCCACHE_STATUS": "started",
        "CT_SCCACHE_STATS_FILE": write(tmp_path, "final.json", snapshot(5, 95, 100)),
        "CT_SCCACHE_BUILD_STATS_FILE": write(tmp_path, "build.json", snapshot(0, 0, 0)),
    }.items():
        monkeypatch.setenv(key, value)
    assert F.main() == 0
    out = capsys.readouterr().out
    assert (
        "::warning title=ci-telemetry-threshold::test-phase sccache hit ratio 0.05"
        in out
    )
    assert out.count("DF12_CI_TELEMETRY") == 1


def test_no_snapshot_on_a_main_push_still_judges_only_disk(tmp_path: Path) -> None:
    """The test-phase fields stay null; `threshold_breaches` reflects disk alone."""
    record = run(tmp_path, None, snapshot(5, 95, 100), MAIN_PUSH)
    assert all(
        record[name] is None for name in T.FIELDS if name != "threshold_breaches"
    )
    assert record["threshold_breaches"] == "none"
