"""Property tests for how the start step merges a caller's sccache config.

The step decides by reading the caller's TOML with a small `awk` program: only
a root `server_startup_timeout_ms`, bare or quoted, means the caller has chosen
a timeout. The same name under a table header, or inside a multi-line string,
does not. The parametrised cases in `test_sccache_server_start.py` are the
readable contract; these generate valid caller documents (unique keys and
tables, comments, whitespace, multi-line strings, table orderings) and hold the
invariants on the parsed result, with the byte comparisons as a second check.
"""

from __future__ import annotations

import dataclasses
import tempfile
import tomllib
import typing as typ
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from test_sccache_server_start import Scenario, _run_server, _written_conf

TIMEOUT_KEY = "server_startup_timeout_ms"
TIMEOUT_LINE = f"{TIMEOUT_KEY} = 60000\n"

_SPELLINGS = (TIMEOUT_KEY, f'"{TIMEOUT_KEY}"')
_BLANKS = st.sampled_from(["", " ", "\t", "  "])
_KEYS = ("alpha", "beta", "gamma", "delta", "epsilon")
_TABLES = ("cache.multilevel", "cache.s3", "dist", "extra")

_COMMENT = st.one_of(
    st.text("abc ", max_size=8).map(lambda text: f"# {text}"),
    st.sampled_from(['# delimiter: """', "# delimiter: '''", "# a # b"]),
)
_FILLER = st.one_of(st.just(""), _COMMENT)


@st.composite
def _timeout_line(draw: st.DrawFn) -> tuple[str, int]:
    """Draw a timeout assignment in either spelling and any spacing."""
    before, after, spelling = (
        draw(_BLANKS),
        draw(_BLANKS),
        draw(st.sampled_from(_SPELLINGS)),
    )
    value = draw(st.integers(1, 99999))
    return f"{before}{spelling}{after}={draw(_BLANKS)}{value}", value


@st.composite
def _decoy(draw: st.DrawFn, key: str) -> str:
    """Draw a multi-line string whose content names the timeout key.

    TOML treats those lines as string content, not as a root setting.
    """
    delimiter = draw(st.sampled_from(['"""', "'''"]))
    return f"{key} = {delimiter}\n{TIMEOUT_KEY} = 123\n{delimiter}"


@st.composite
def _table(draw: st.DrawFn, name: str) -> str:
    """Draw a table whose body may name the timeout key, which is not root."""
    keys = draw(st.lists(st.sampled_from(_KEYS), unique=True, max_size=3))
    body = [f"{key} = {draw(st.integers(0, 99))}" for key in keys]
    if draw(st.booleans()):
        body.append(f"{draw(st.sampled_from(_SPELLINGS))} = {draw(st.integers(1, 99))}")
    return "\n".join([f"[{name}]", *body])


@dataclasses.dataclass(frozen=True)
class Document:
    """A generated caller config and the timeout its root chose, if any."""

    text: str
    root_timeout: int | None


@st.composite
def documents(draw: st.DrawFn) -> Document:
    """Draw a valid caller config: root section first, then unique tables."""
    keys = draw(st.lists(st.sampled_from(_KEYS), unique=True, max_size=4))
    root = [f"{key} = {draw(st.integers(0, 99))}" for key in keys]
    decoy_keys = [key for key in _KEYS if key not in keys]
    if decoy_keys and draw(st.booleans()):
        root.append(draw(_decoy(draw(st.sampled_from(decoy_keys)))))
    root_timeout = None
    if draw(st.booleans()):
        line, root_timeout = draw(_timeout_line())
        root.insert(draw(st.integers(0, len(root))), line)
    for filler in draw(st.lists(_FILLER, max_size=3)):
        root.insert(draw(st.integers(0, len(root))), filler)
    names = draw(st.lists(st.sampled_from(_TABLES), unique=True, max_size=3))
    tables = [draw(_table(name)) for name in names]
    return Document("\n".join([*root, *tables]) + "\n", root_timeout)


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


def _without_timeout(parsed: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the parsed settings other than the root timeout."""
    return {key: value for key, value in parsed.items() if key != TIMEOUT_KEY}


@settings(max_examples=60, deadline=None)
@given(documents())
def test_the_merged_root_timeout_is_the_callers_or_the_default(
    document: Document,
) -> None:
    """The merged file is valid TOML with exactly one meaningful root timeout.

    A root timeout the caller chose passes the file through unchanged. Absent
    one, the default is prepended exactly, so nested occurrences and string
    content never suppress it, and every other setting survives untouched.
    """
    caller = tomllib.loads(document.text)
    merged_text = _merge(document.text)
    merged = tomllib.loads(merged_text)

    if document.root_timeout is None:
        assert merged[TIMEOUT_KEY] == 60000
        assert merged_text == TIMEOUT_LINE + document.text
    else:
        assert merged[TIMEOUT_KEY] == document.root_timeout
        assert merged_text == document.text
    assert _without_timeout(merged) == _without_timeout(caller)


def test_the_generator_reaches_the_cases_the_property_is_about() -> None:
    """A property over a generator that never draws the hard cases is idle."""
    seen: list[Document] = []

    @settings(max_examples=300, deadline=None, database=None)
    @given(documents())
    def draw(document: Document) -> None:
        seen.append(document)

    draw()

    assert any(doc.root_timeout is not None for doc in seen)
    assert any(doc.root_timeout is None and '"""' in doc.text for doc in seen)
    assert any(doc.root_timeout is None and "'''" in doc.text for doc in seen)
    assert any(
        doc.root_timeout is None and TIMEOUT_KEY in "".join(doc.text.partition("[")[2:])
        for doc in seen
    )
