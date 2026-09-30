"""Property tests for how the start step merges a caller's sccache config.

The step decides by reading the caller's TOML with a small `awk` program: only
a root `server_startup_timeout_ms`, bare or quoted, means the caller has chosen
a timeout, and one nested under a table header does not. The parametrised cases
in `test_sccache_server_start.py` are the readable contract; these generate the
wider set of layouts (comments, blank lines, whitespace, unrelated keys, table
orderings) to hold the same invariants across them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from test_sccache_server_start import Scenario, _run_server, _written_conf

TIMEOUT_LINE = "server_startup_timeout_ms = 60000\n"

_SPELLINGS = ("server_startup_timeout_ms", '"server_startup_timeout_ms"')
_BLANKS = st.sampled_from(["", " ", "\t", "  "])

#: A root-level line that is not the timeout: a comment, a blank, or a setting.
_UNRELATED_ROOT = st.one_of(
    st.just(""),
    st.text("abc ", max_size=8).map(lambda text: f"# {text}"),
    st.tuples(st.sampled_from(["alpha", "beta", "gamma"]), st.integers(0, 99)).map(
        lambda pair: f"{pair[0]} = {pair[1]}"
    ),
)

#: A caller-chosen root timeout, in either spelling and any spacing.
_ROOT_TIMEOUT = st.tuples(
    _BLANKS, st.sampled_from(_SPELLINGS), _BLANKS, _BLANKS, st.integers(1, 99999)
).map(lambda parts: f"{parts[0]}{parts[1]}{parts[2]}={parts[3]}{parts[4]}")

#: A table whose body may name the timeout key. Under a header it is not root.
_TABLE = st.tuples(
    st.sampled_from(["cache.multilevel", "cache.s3", "dist"]),
    st.lists(
        st.one_of(
            _UNRELATED_ROOT,
            st.tuples(_BLANKS, st.sampled_from(_SPELLINGS), st.integers(1, 99)).map(
                lambda parts: f"{parts[0]}{parts[1]} = {parts[2]}"
            ),
        ),
        max_size=3,
    ),
).map(lambda pair: "\n".join([f"[{pair[0]}]", *pair[1]]))


def _merge(caller_config: str) -> str:
    """Run the start step over `caller_config` and return the file it exports."""
    with tempfile.TemporaryDirectory() as directory:
        workdir = Path(directory)
        binary = workdir / "sccache"
        binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        theirs = workdir / "theirs.toml"
        theirs.write_text(caller_config, encoding="utf-8")
        completed = _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(binary),
                caller_conf=str(theirs),
            )
        )
        assert completed.returncode == 0, completed.stderr
        return _written_conf(workdir)


def _document(root: list[str], tables: list[str]) -> str:
    """Join root lines, then tables, into one TOML document."""
    return "\n".join([*root, *tables]) + "\n"


@settings(max_examples=40, deadline=None)
@given(
    before=st.lists(_UNRELATED_ROOT, max_size=4),
    timeout=_ROOT_TIMEOUT,
    after=st.lists(_UNRELATED_ROOT, max_size=3),
    tables=st.lists(_TABLE, max_size=3),
)
def test_a_root_timeout_the_caller_chose_is_kept_unchanged(
    before: list[str], timeout: str, after: list[str], tables: list[str]
) -> None:
    """Wherever it sits among the root lines, the caller's file passes through."""
    document = _document([*before, timeout, *after], tables)

    assert _merge(document) == document


@settings(max_examples=40, deadline=None)
@given(
    root=st.lists(_UNRELATED_ROOT, max_size=4),
    tables=st.lists(_TABLE, max_size=3),
)
def test_an_absent_root_timeout_gets_the_default_prepended_exactly(
    root: list[str], tables: list[str]
) -> None:
    """Nested occurrences never suppress the root default.

    The default lands first, so it stays a root key however the caller's tables
    are ordered, and everything else is preserved byte for byte.
    """
    document = _document(root, tables)

    assert _merge(document) == TIMEOUT_LINE + document


def test_the_strategies_can_draw_a_nested_timeout() -> None:
    """The table strategy must be able to name the key, or the property is idle."""
    seen: list[str] = []

    @settings(max_examples=200, deadline=None, database=None)
    @given(_TABLE)
    def draw(table: str) -> None:
        seen.append(table)

    draw()

    assert any("server_startup_timeout_ms" in table for table in seen)
