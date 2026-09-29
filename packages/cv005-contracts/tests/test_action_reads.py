"""Cases for reading the environment keys an action takes from its caller.

The parity rule leaves out keys that only pin or place a tool, on the ground
that the action never reads them. These cases plant a read in a fixture
action, in each place a read can hide, and show it found; and plant lookalikes
that are not reads, which must not be.
"""

from __future__ import annotations

import textwrap
import typing as typ

import pytest
from cv005_contracts import parity
from cv005_contracts.action_reads import excluded_reads, keys_read, pattern_reads

if typ.TYPE_CHECKING:
    from pathlib import Path

ACTION: typ.Final[str] = textwrap.dedent("""\
    name: Fixture
    runs:
      using: composite
      steps:
        - name: Run
          shell: bash
          env:
            INPUT_VERSION: ${{ inputs.version }}
          run: echo "$HOME"
    """)


def _action(root: Path, *, yml: str = ACTION, script: str | None = None) -> Path:
    """Write a fixture action, with one script when given, and return it."""
    root.mkdir(parents=True)
    (root / "action.yml").write_text(yml, encoding="utf-8")
    if script is not None:
        (root / "scripts").mkdir()
        (root / "scripts" / "run.py").write_text(script, encoding="utf-8")
    return root


def test_a_clean_action_reads_nothing_excluded(tmp_path: Path) -> None:
    """The fixture reads `HOME` and sets `INPUT_VERSION`; neither is excluded."""
    action = _action(tmp_path / "a", script='import os\nos.environ.get("HOME")\n')
    assert excluded_reads(action) == []


@pytest.mark.parametrize(
    "script",
    [
        'import os\nos.environ.get("FOO_VERSION")\n',
        'import os\nos.environ["FOO_VERSION"]\n',
        'import os\nos.getenv("FOO_VERSION", "")\n',
        'def read(env):\n    return env.get("FOO_VERSION")\n',
        'import os\nvalue = os.environ.get(f"{1}" and "FOO_VERSION")\n',
    ],
)
def test_a_planted_python_read_is_found(tmp_path: Path, script: str) -> None:
    """Every spelling of a lookup in a script reaches a string constant."""
    action = _action(tmp_path / "a", script=script)
    assert excluded_reads(action) == ["FOO_VERSION"]


@pytest.mark.parametrize(
    "run",
    [
        'run: echo "$FOO_VERSION"',
        'run: echo "${FOO_VERSION:-x}"',
        "run: echo ${{ env.FOO_VERSION }}",
    ],
)
def test_a_planted_action_read_is_found(tmp_path: Path, run: str) -> None:
    """A shell expansion or an `env` context reference in the YAML is a read."""
    action = _action(tmp_path / "a", yml=ACTION.replace('run: echo "$HOME"', run))
    assert excluded_reads(action) == ["FOO_VERSION"]


@pytest.mark.parametrize(
    "key",
    ["UV_CACHE_DIR", "UV_TOOL_DIR", "CARGO_NET_RETRY", "TOOL_REV", "X_SHA256_ARM"],
)
def test_every_kind_of_excluded_key_is_found(tmp_path: Path, key: str) -> None:
    """Placement and retry keys are read as well as version, revision, checksum."""
    action = _action(tmp_path / "a", script=f'import os\nos.environ.get("{key}")\n')
    assert excluded_reads(action) == [key]


def test_a_key_the_action_assigns_itself_is_not_a_read(tmp_path: Path) -> None:
    """A step's own `env:` replaces the caller's value, so the caller cannot vary it."""
    yml = ACTION.replace("INPUT_VERSION:", "FOO_VERSION:")
    action = _action(
        tmp_path / "a", yml=yml, script='import os\nos.environ.get("FOO_VERSION")\n'
    )
    assert excluded_reads(action) == []


def test_a_mention_in_a_comment_or_prose_is_not_a_read(tmp_path: Path) -> None:
    """Only string constants that are names count, not comments or sentences."""
    script = (
        '"""Uses FOO_VERSION to pick a tool."""\n'
        "# FOO_VERSION\n"
        'x = "the FOO_VERSION pin"\n'
    )
    action = _action(tmp_path / "a", script=script)
    assert excluded_reads(action) == []


def test_a_test_or_cache_directory_is_not_action_code(tmp_path: Path) -> None:
    """Tests and bytecode caches hold names that are not what the action reads."""
    action = _action(tmp_path / "a", script="x = 1\n")
    for name in ("tests", "__pycache__"):
        (action / "scripts" / name).mkdir()
        (action / "scripts" / name / "t.py").write_text(
            '"FOO_VERSION"\n', encoding="utf-8"
        )
    assert excluded_reads(action) == []


def test_an_action_without_metadata_is_refused(tmp_path: Path) -> None:
    """An empty reading would find nothing and so pass."""
    with pytest.raises(FileNotFoundError):
        keys_read(tmp_path)


def test_a_key_the_action_reads_is_compared_once_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing a read key in the carve-out makes it measured, and the finding clears."""
    action = _action(
        tmp_path / "a", script='import os\nos.environ.get("FOO_VERSION")\n'
    )
    assert excluded_reads(action) == ["FOO_VERSION"]
    monkeypatch.setattr(parity, "ACTION_READ_KEYS", frozenset({"FOO_VERSION"}))
    assert excluded_reads(action) == []
    assert pattern_reads(action) == {"FOO_VERSION"}


def test_a_key_the_patterns_do_not_match_is_not_reported(tmp_path: Path) -> None:
    """`UV_PYTHON` and `CARGO_HOME` are measured, so reading them is fine."""
    action = _action(
        tmp_path / "a",
        script='import os\nos.environ.get("UV_PYTHON")\nos.environ.get("CARGO_HOME")\n',
    )
    assert excluded_reads(action) == []
    assert pattern_reads(action) == set()
