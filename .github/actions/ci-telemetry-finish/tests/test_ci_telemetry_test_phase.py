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
from hypothesis import given, settings
from hypothesis import strategies as st

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


AMBIENT = (
    "GITHUB_STEP_SUMMARY",
    "GITHUB_OUTPUT",
    "GITHUB_JOB",
    "GITHUB_REPOSITORY",
    "GITHUB_WORKFLOW",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_SHA",
    "CT_TEST_HIT_RATIO_MIN",
    "CT_TEST_MIN_COMPILE_REQUESTS",
    "CT_MIN_FREE_DISK_MIB",
    "CT_SCCACHE_BUILD_STATS_FILE",
    "CT_THRESHOLDS_MODE",
)
META = {
    "format": 1,
    "sampler": "proc-stat",
    "nproc": 4,
    "clk_tck": 100,
    "interval_seconds": 10,
    "disk_path": ".",
    "started_ns": 1_000_000_000_000,
}


def sample(stamp: int, free_kib: int) -> str:
    """Return one sampler line: stamp, ten CPU counters, memory and free disk."""
    cpu = ["5004500", "1000", "2000500", "90015000", "40000", "500", "30000"]
    fields = [str(stamp), *cpu, "7000", "0", "0", "16384000", "12288000", str(free_kib)]
    return "\t".join(fields)


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
    for ambient in AMBIENT:
        monkeypatch.delenv(ambient, raising=False)
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


def low_disk_state(tmp_path: Path, free_mib: int) -> None:
    """Write sampler state whose least free disk is *free_mib* MiB."""
    directory = tmp_path / "ci-telemetry"
    directory.mkdir()
    (directory / "meta.json").write_text(json.dumps(META), encoding="utf-8")
    lines = [sample(1_000_000_000_000 + i * 10**10, free_mib * 1024) for i in range(3)]
    (directory / "samples.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> list[str]:
    """Run ``finish.main`` under *env* alone and return its warning lines."""
    for ambient in AMBIENT:
        monkeypatch.delenv(ambient, raising=False)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(F, "take_sample", lambda _disk: sample(2 * 10**12, 10**9))
    assert F.main() == 0
    out = capsys.readouterr().out
    assert out.count("DF12_CI_TELEMETRY") == 1
    return [line for line in out.splitlines() if "ci-telemetry-threshold" in line]


def test_a_low_disk_breaches_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 GiB free on a main push warns about disk, exactly once, and exits 0."""
    low_disk_state(tmp_path, 1024)
    warnings = run_main(tmp_path, monkeypatch, {**MAIN_PUSH}, capsys)
    assert warnings == [
        (
            "::warning title=ci-telemetry-threshold::least free disk 1024 MiB is "
            "below 3072 MiB"
        )
    ]


def test_both_breaches_warn_in_a_fixed_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hit-ratio warning precedes the disk warning, and the job still passes."""
    low_disk_state(tmp_path, 1024)
    env = {
        **MAIN_PUSH,
        "CT_SCCACHE_STATUS": "started",
        "CT_SCCACHE_STATS_FILE": write(tmp_path, "final.json", snapshot(5, 95, 100)),
        "CT_SCCACHE_BUILD_STATS_FILE": write(tmp_path, "b.json", snapshot(0, 0, 0)),
    }
    warnings = run_main(tmp_path, monkeypatch, env, capsys)
    assert [w.split("::")[2].split(" ")[0] for w in warnings] == ["test-phase", "least"]


def test_an_unreadable_snapshot_is_named(tmp_path: Path) -> None:
    """A snapshot path that is a directory cannot be read: `snapshot-unreadable`."""
    record = run(tmp_path, None, snapshot(10, 2, 20))
    assert record["sccache_test_problem"] is None
    env = {
        "CT_SCCACHE_STATUS": "started",
        "CT_SCCACHE_STATS_FILE": write(tmp_path, "final.json", snapshot(10, 2, 20)),
        "CT_SCCACHE_BUILD_STATS_FILE": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
    }
    record = F.finish(tmp_path / "state", env)
    assert record["sccache_test_problem"] == "snapshot-unreadable"
    assert record["sccache_test_hit_ratio"] is None


def test_an_unknown_mode_warns_and_judges_nothing() -> None:
    """A typo in `thresholds` is reported instead of silently disabling the check."""
    env = {**MAIN_PUSH, "CT_THRESHOLDS_MODE": "mian-push"}
    assert T.judge({"min_free_disk_mib": 1}, env) == {"threshold_breaches": None}
    (line,) = T.annotations({"min_free_disk_mib": 1}, env)
    assert "unknown thresholds mode 'mian-push'" in line


def test_never_stays_silent() -> None:
    """`never` is a valid mode and draws no configuration warning."""
    env = {**MAIN_PUSH, "CT_THRESHOLDS_MODE": "never"}
    assert T.annotations({"min_free_disk_mib": 1}, env) == []


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_non_finite_numbers_fall_back_to_the_default(bad: str) -> None:
    """NaN would suppress the comparison; it reads as the default instead."""
    env = {**MAIN_PUSH, "CT_TEST_HIT_RATIO_MIN": bad}
    record = {
        "sccache_test_hits": 5,
        "sccache_test_misses": 95,
        "sccache_test_compile_requests": 100,
    }
    assert T.breaches(record, env) == ["test_hit_ratio"]


def test_the_exact_ratio_is_judged_not_the_rounded_one() -> None:
    """19,999 hits and 5,001 misses is 0.79996: recorded as 0.8, still below 0.80."""
    fields = T.delta(
        {
            "sccache_hits": 19999,
            "sccache_misses": 5001,
            "sccache_compile_requests": 25000,
        },
        {"sccache_hits": 0, "sccache_misses": 0, "sccache_compile_requests": 0},
    )
    assert fields["sccache_test_hit_ratio"] == 0.8
    assert T.breaches(fields, MAIN_PUSH) == ["test_hit_ratio"]


def test_the_warning_uses_the_env_it_was_given(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller's mapping decides both the breach and the floor the warning names."""
    monkeypatch.setenv("CT_TEST_HIT_RATIO_MIN", "0.10")
    record = {
        "sampler": "proc-stat",
        "threshold_breaches": "test_hit_ratio",
        "sccache_test_hit_ratio": 0.5,
        "sccache_test_compile_requests": 100,
    }
    F.print_marker(record, {"CT_TEST_HIT_RATIO_MIN": "0.99"})
    assert "is below 0.99" in capsys.readouterr().out


counter = st.integers(min_value=0, max_value=10**9)


@settings(max_examples=100)
@given(
    build=st.tuples(counter, counter, counter),
    gain=st.tuples(counter, counter, counter),
)
def test_delta_is_the_exact_difference(
    build: tuple[int, int, int], gain: tuple[int, int, int]
) -> None:
    """Non-decreasing counters give exact deltas and a rounded ratio or None."""
    keys = ("sccache_hits", "sccache_misses", "sccache_compile_requests")
    before = dict(zip(keys, build, strict=True))
    after = {k: b + g for (k, b), g in zip(before.items(), gain, strict=True)}
    fields = T.delta(after, before)
    assert (
        fields["sccache_test_hits"],
        fields["sccache_test_misses"],
        fields["sccache_test_compile_requests"],
    ) == gain
    attempts = gain[0] + gain[1]
    expected = round(gain[0] / attempts, 4) if attempts else None
    assert fields["sccache_test_hit_ratio"] == expected
    assert fields["sccache_test_problem"] is None


@settings(max_examples=100)
@given(
    build=st.tuples(counter, counter, counter),
    drop=st.integers(min_value=0, max_value=2),
    by=st.integers(min_value=1, max_value=10**6),
)
def test_a_lower_final_counter_is_a_reset(
    build: tuple[int, int, int], drop: int, by: int
) -> None:
    """Any final counter below its snapshot reports `counter-reset`."""
    keys = ("sccache_hits", "sccache_misses", "sccache_compile_requests")
    before = dict(zip(keys, build, strict=True))
    after = dict(before)
    after[keys[drop]] = before[keys[drop]] - by
    fields = T.delta(after, before)
    assert fields["sccache_test_problem"] == "counter-reset"
    assert fields["sccache_test_hit_ratio"] is None


BREACH_RECORDS = {
    "none": {
        "min_free_disk_mib": 9000,
        "sccache_test_hits": 99,
        "sccache_test_misses": 1,
        "sccache_test_compile_requests": 100,
    },
    "test_hit_ratio": {
        "min_free_disk_mib": 9000,
        "sccache_test_hits": 5,
        "sccache_test_misses": 95,
        "sccache_test_compile_requests": 100,
        "sccache_test_hit_ratio": 0.05,
    },
    "min_free_disk": {
        "min_free_disk_mib": 1024,
        "sccache_test_hits": 99,
        "sccache_test_misses": 1,
        "sccache_test_compile_requests": 100,
    },
    "both": {
        "min_free_disk_mib": 1024,
        "sccache_test_hits": 5,
        "sccache_test_misses": 95,
        "sccache_test_compile_requests": 100,
        "sccache_test_hit_ratio": 0.05,
    },
}


@pytest.mark.parametrize("name", list(BREACH_RECORDS))
def test_breach_states_and_warnings_are_pinned(name: str, snapshot) -> None:  # noqa: ANN001 - syrupy fixture
    """The breach field and the exact warning lines for each state are snapshotted."""
    record = dict(BREACH_RECORDS[name])
    record.update(T.judge(record, MAIN_PUSH))
    assert {
        "threshold_breaches": record["threshold_breaches"],
        "warnings": T.annotations(record, MAIN_PUSH),
    } == snapshot
