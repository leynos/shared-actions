"""Property-based tests for the mutation workflow helper scripts.

These complement the example-based unit tests with range invariants over
CSV splitting, file bucketing, module-glob translation, mutmut results
parsing, and outcome counting. Filesystem-bound behaviour (git scans,
artefact merging) stays example-based because Hypothesis does not mix
with function-scoped fixtures such as ``tmp_path``.
"""

from __future__ import annotations

import unittest.mock as mock

from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts import graphql_client
from workflow_scripts import mutation_detect_changes as detect
from workflow_scripts import mutation_run_mutmut as run_mutmut
from workflow_scripts import mutation_summarize_cargo as summarize

SEGMENT = st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True)
STATUS = st.sampled_from(["killed", "survived", "no tests", "timeout", "suspicious"])
COUNTED = st.sampled_from(["CaughtMutant", "MissedMutant", "Timeout", "Unviable"])


@given(
    attempt=st.integers(min_value=0, max_value=20),
    max_retries=st.integers(min_value=0, max_value=20),
)
def test_retry_budget_is_strict(
    attempt: int,
    max_retries: int,
) -> None:
    """Exactly the attempts below the retry limit have budget remaining."""
    assert graphql_client._should_retry(attempt, max_retries) is (attempt < max_retries)


@given(
    attempt=st.integers(min_value=0, max_value=20),
    base_seconds=st.integers(min_value=1, max_value=10),
)
def test_backoff_is_base_times_power_of_two(
    attempt: int,
    base_seconds: int,
) -> None:
    """Backoff grows exponentially from the supplied base duration."""
    recorded: list[float] = []
    with mock.patch.object(graphql_client.time, "sleep", recorded.append):
        graphql_client._backoff_sleep(attempt, base_seconds)
    assert recorded == [base_seconds * (2**attempt)]


@given(
    extras=st.lists(SEGMENT, unique=True, max_size=8),
    include_root=st.booleans(),
)
def test_scoped_matrix_orders_root_first_then_alphabetically(
    extras: list[str],
    *,
    include_root: bool,
) -> None:
    """Root leads every scoped matrix and remaining targets are alphabetical."""
    targets = [*extras, *(["."] if include_root else [])]
    buckets = {target: [f"{target}/src/lib.rs"] for target in reversed(targets)}
    entries = detect.scoped_run_matrix(buckets, detect.DetectionConfig())
    expected = sorted(targets, key=lambda target: (target != ".", target))
    assert [entry.dir for entry in entries] == expected


@given(tokens=st.lists(SEGMENT, max_size=8))
def test_split_csv_round_trips_clean_tokens(tokens: list[str]) -> None:
    """Joining tokens with padded commas and splitting returns them."""
    joined = " , ".join(tokens)
    assert detect.split_csv(joined) == tuple(tokens)


@given(
    root_files=st.lists(SEGMENT.map(lambda s: f"src/{s}.rs"), max_size=5),
    extra_files=st.lists(SEGMENT.map(lambda s: f"extra/src/{s}.rs"), max_size=5),
    other_files=st.lists(SEGMENT.map(lambda s: f"docs/{s}.rs"), max_size=5),
)
def test_bucket_files_partitions_without_loss_or_overlap(
    root_files: list[str], extra_files: list[str], other_files: list[str]
) -> None:
    """Buckets are disjoint, complete for matches, and exclude the rest."""
    config = detect.DetectionConfig(extra_crate_dirs=("extra",))
    buckets = detect.bucket_files([*root_files, *extra_files, *other_files], config)
    assert sorted(buckets.get(".", [])) == sorted(root_files)
    assert sorted(buckets.get("extra", [])) == sorted(extra_files)
    bucketed = [name for names in buckets.values() for name in names]
    assert len(bucketed) == len(root_files) + len(extra_files)
    assert not (set(buckets.get(".", ())) & set(buckets.get("extra", ())))


@given(
    paths=st.lists(
        st.lists(SEGMENT, min_size=1, max_size=3).map(
            lambda parts: "src/" + "/".join(parts) + ".py"
        ),
        max_size=6,
    )
)
def test_module_globs_are_deduplicated_module_patterns(paths: list[str]) -> None:
    """Globs are unique module patterns with no path or suffix residue."""
    globs = run_mutmut.files_to_module_globs(" ".join(paths), "src/")
    assert len(globs) == len(set(globs))
    for glob in globs:
        assert glob.endswith(".*")
        assert "/" not in glob
        assert ".py" not in glob
    assert len(globs) <= len(paths)


@given(
    entries=st.lists(
        st.tuples(
            st.lists(SEGMENT, min_size=1, max_size=3), st.integers(0, 99), STATUS
        ),
        max_size=8,
    )
)
def test_parse_results_round_trips_rendered_lines(
    entries: list[tuple[list[str], int, str]],
) -> None:
    """Rendered result lines parse back to the same names and statuses."""
    expected: list[run_mutmut.MutantResult] = []
    lines = ["warning: some noise", ""]
    for parts, index, status in entries:
        name = ".".join([*parts, f"x_f__mutmut_{index}"])
        expected.append(run_mutmut.MutantResult(name=name, status=status))
        lines.append(f"    {name}: {status}")
    parsed = run_mutmut.parse_results("\n".join(lines))
    assert parsed == expected


@given(summaries=st.lists(COUNTED, max_size=20))
def test_parse_outcomes_counts_match_entries(summaries: list[str]) -> None:
    """Counts sum to the entry total; survivors equal the missed count."""
    payload: dict[str, object] = {
        "outcomes": [
            {"scenario": "Baseline", "summary": "Success"},
            *(
                {
                    "scenario": {"Mutant": {"file": "f.rs", "name": "m"}},
                    "summary": summary,
                }
                for summary in summaries
            ),
        ]
    }
    counts, survivors = summarize.parse_outcomes(payload)
    assert sum(counts.values()) == len(summaries)
    assert len(survivors) == counts["MissedMutant"]
    assert counts["MissedMutant"] == summaries.count("MissedMutant")


@given(
    files=st.lists(SEGMENT.map(lambda s: f"extra/src/{s}.rs"), min_size=1, max_size=5)
)
def test_scoped_matrix_strips_target_dir_prefix(files: list[str]) -> None:
    """Scoped entries carry paths relative to their target directory."""
    config = detect.DetectionConfig(extra_crate_dirs=("extra",))
    entries = detect.scoped_run_matrix({"extra": files}, config)
    assert len(entries) == 1
    for relative in entries[0].files.split():
        assert not relative.startswith("extra/")
        assert relative.startswith("src/")
