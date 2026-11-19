"""What a command printed: a bounded stream and the version token it names.

A stream can be longer than the preflight retains, so the retained prefix must
still be readable as the output the command wrote, and a version token cut by
the bound must never satisfy a pin.
"""

from __future__ import annotations

import pytest

from workflow_scripts.mutationpreflight.constants import LOG_BOUND_BYTES
from workflow_scripts.mutationpreflight.models import Ran
from workflow_scripts.mutationpreflight.running import (
    TRUNCATION_PREFIX,
    bounded,
    complete_stream,
    last_line,
    reports_version,
)


class TestVersionMatching:
    """A reported version is compared as a token, not searched for as text."""

    @pytest.mark.parametrize(
        ("printed", "wanted", "accepted"),
        [
            ("mold 2.41.0", "2.4", False),
            ("mold 2.4.1", "2.4", True),
            ("mold 2.4", "2.4", True),
            ("mold 1.2.3rc1", "1.2.3", False),
            ("mold 1.2.3.post1", "1.2.3", False),
            ("mold 1.2.3beta1", "1.2.3", False),
            ("mold 1.2.3-rc1", "1.2.3", False),
            ("mold 1.2.3_rc1", "1.2.3", False),
            ("mold 1.2.3-beta", "1.2.3", False),
            ("mold 1.2.3", "1.2.3", True),
            ("mold 0.9.0rc1", "0.9.0rc1", True),
            ("mold 0.9.0rc1", "0.9.0", False),
            ("mold 1.2.3build", "1.2.3", True),
            ("mold 1.2.30", "1.2.3", False),
            ("mold 1.2.3rc1", "1.2.3rc1", True),
            ("mold 1.2.3rc1.dev1", "1.2.3rc1", False),
            ("mold 1.2.3rc1.dev1", "1.2.3", False),
            ("mold 1.2.3.rc1", "1.2.3", False),
        ],
    )
    def test_a_token_must_equal_the_declared_version_or_extend_it(
        self, printed: str, wanted: str, *, accepted: bool
    ) -> None:
        """A pre- or post-release suffix stays part of the token it qualifies."""
        assert reports_version(printed, wanted) is accepted


class TestBoundedOutput:
    """A truncated stream still names a line of its retained output."""

    def test_the_truncation_marker_is_not_the_diagnostic_line(self) -> None:
        """The marker counts dropped bytes; it does not say what failed."""
        text = "bad line\n" + "x" * (LOG_BOUND_BYTES + 1)
        outcome = Ran(exit_code=1, stdout="", stderr=bounded(text))

        assert "bytes truncated" in outcome.stderr
        assert "bytes truncated" not in last_line(outcome)
        assert last_line(outcome)

    def test_a_stream_the_bound_holds_exactly_is_not_truncated(self) -> None:
        """The bound is a ceiling, so a stream exactly at it is complete."""
        text = "x" * LOG_BOUND_BYTES

        assert bounded(text) == text, (
            "a stream the bound holds exactly must be kept whole"
        )
        assert "bytes truncated" not in bounded(text)

    def test_one_byte_past_the_bound_is_truncated(self) -> None:
        """A byte over the ceiling is counted and marked, not lost silently."""
        text = "x" * (LOG_BOUND_BYTES + 1)

        kept = bounded(text)

        assert kept.partition("\n")[0] == "x" * LOG_BOUND_BYTES, (
            "the retained prefix must be the bound's worth of bytes"
        )
        assert "bytes truncated" in kept, (
            "a stream over the bound must say how much was dropped"
        )

    def test_a_token_cut_at_the_bound_is_not_matched(self) -> None:
        """A version that ends at the bound is unproven, so it is not a match."""
        marker = f"{TRUNCATION_PREFIX}900 bytes truncated]"

        cut = complete_stream(f"probe 2.4{marker}")
        earlier = complete_stream(f"probe 2.41.0\n2.4{marker}")

        assert not reports_version(cut, "2.4"), (
            "the token the truncation may have extended must not satisfy a pin"
        )
        assert reports_version(earlier, "2.41.0"), (
            "a complete token printed before the flood still counts"
        )
