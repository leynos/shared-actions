"""The parity rule's tool-pin exclusion holds only while the action reads none.

`cv005-contracts` leaves environment keys named `*_VERSION`, `*_REV`,
`*_SHA256*`, or placing uv and Cargo's network settings out of the comparison
between a pull-request lane and the publisher, on the ground that no such key
changes what `generate-coverage` measures. That is a claim about this
directory's action, so this contract reads the action at the commit under test
and fails if it reads any excluded key. The exclusion is handwritten; the
action is not, and a later edit to the action could otherwise make two
differently pinned legs measure differently while the rule calls them equal.
"""

from __future__ import annotations

import shutil
import sys
import typing as typ
from pathlib import Path

REPOSITORY: typ.Final[Path] = Path(__file__).resolve().parents[2]
ACTION: typ.Final[Path] = REPOSITORY / ".github" / "actions" / "generate-coverage"
LIBRARY: typ.Final[Path] = REPOSITORY / "packages" / "cv005-contracts"

# The library is not installed in this environment; it is read from the tree,
# which is also the commit a consumer pins.
sys.path.insert(0, str(LIBRARY))

from cv005_contracts import parity  # noqa: E402 - needs the path added above.
from cv005_contracts.action_reads import (  # noqa: E402 - as above.
    excluded_reads,
    keys_read,
    pattern_reads,
)


def test_the_action_reads_no_key_the_exclusion_leaves_out() -> None:
    """Every pin or placement key the action reads is compared by the rule."""
    assert excluded_reads(ACTION) == []


def test_the_carve_out_is_exactly_what_the_action_reads() -> None:
    """A carve-out entry the action does not read would compare a key for nothing."""
    assert pattern_reads(ACTION) == set(parity.ACTION_READ_KEYS)


def test_the_reading_finds_the_action_files() -> None:
    """An empty reading would pass both checks above; it must see real reads."""
    keys = keys_read(ACTION)
    assert {"UV_PYTHON", "GITHUB_OUTPUT"} <= keys, sorted(keys)


def test_a_planted_read_of_an_excluded_key_fails_the_contract(tmp_path: Path) -> None:
    """Copy the real action, add a read of `FOO_VERSION`, and see it refused."""
    planted = tmp_path / "generate-coverage"
    shutil.copytree(ACTION, planted, ignore=shutil.ignore_patterns("__pycache__"))
    script = planted / "scripts" / "run_python.py"
    script.write_text(
        script.read_text(encoding="utf-8")
        + '\nimport os\n_PLANTED = os.environ.get("FOO_VERSION")\n',
        encoding="utf-8",
    )
    assert excluded_reads(planted) == ["FOO_VERSION"]
    assert excluded_reads(ACTION) == [], "the real action must be untouched"
