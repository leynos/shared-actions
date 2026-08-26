"""Regression tests for the committed spelling policy."""

from pathlib import Path
import tomllib

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_INLINE_CODE_IGNORE_PATTERN = r"`[^`\n]+`"


def test_committed_spelling_policy_ignores_inline_code() -> None:
    """Keep the checked-in overlay's inline-code exemption in force."""
    with (_REPOSITORY_ROOT / "typos.local.toml").open("rb") as policy_file:
        policy = tomllib.load(policy_file)

    assert _INLINE_CODE_IGNORE_PATTERN in policy["patterns"]["ignore"], (
        "committed spelling policy must preserve its inline-code exemption"
    )
