"""What a command printed: a bounded stream and the version token it names.

A stream can be longer than the preflight retains, so the retained prefix must
still be readable as the output the command wrote, and a version token cut by
the bound must never satisfy a pin.
"""

from __future__ import annotations

import pytest

from workflow_scripts.mutationpreflight.constants import LOG_BOUND_BYTES
from workflow_scripts.mutationpreflight.diagnostics import (
    TRUNCATION_PREFIX,
    bounded,
    complete_stream,
    last_line,
    reports_version,
)
from workflow_scripts.mutationpreflight.models import Ran


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
        assert reports_version(printed, wanted) is accepted, (
            f"reports_version({printed!r}, {wanted!r}) must be {accepted}"
        )


class TestBoundedOutput:
    """A truncated stream still names a line of its retained output."""

    def test_the_truncation_marker_is_not_the_diagnostic_line(self) -> None:
        """The marker counts dropped bytes; it does not say what failed."""
        text = "bad line\n" + "x" * (LOG_BOUND_BYTES + 1)
        outcome = Ran(exit_code=1, stdout="", stderr=bounded(text))

        assert "bytes truncated" in outcome.stderr, (
            "a bounded stream must say how many bytes were dropped"
        )
        assert "bytes truncated" not in last_line(outcome), (
            "the marker counts bytes; it must not be read as the failing line"
        )
        assert last_line(outcome), (
            "a truncated stream must still name the line it failed on"
        )

    def test_a_stream_the_bound_holds_exactly_is_not_truncated(self) -> None:
        """The bound is a ceiling, so a stream exactly at it is complete."""
        text = "x" * LOG_BOUND_BYTES

        assert bounded(text) == text, (
            "a stream the bound holds exactly must be kept whole"
        )
        assert "bytes truncated" not in bounded(text), (
            "a stream the bound holds exactly must not be marked as truncated"
        )

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

        cut = complete_stream(f"probe 2.4\n{marker}", truncated=True)
        earlier = complete_stream(f"probe 2.41.0\n2.4\n{marker}", truncated=True)

        assert not reports_version(cut, "2.4"), (
            "the token the truncation may have extended must not satisfy a pin"
        )
        assert reports_version(earlier, "2.41.0"), (
            "a complete token printed before the flood still counts"
        )

    def test_an_untruncated_stream_is_returned_unchanged(self) -> None:
        """Output that resembles the marker is not evidence the stream was cut."""
        printed = f"probe 2.4\n{TRUNCATION_PREFIX}900 bytes truncated]"

        kept = complete_stream(printed, truncated=False)

        assert kept == printed, (
            "only a stream the run dropped bytes from may be read as truncated"
        )
        assert reports_version(kept, "2.4"), (
            "a token in a complete stream must still satisfy a pin"
        )

    def test_a_token_followed_by_whitespace_is_complete(self) -> None:
        """Whitespace before the marker ends the token, so it is kept."""
        marker = f"{TRUNCATION_PREFIX}900 bytes truncated]"

        kept = complete_stream(f"probe 2.4  \n{marker}", truncated=True)

        assert kept == "probe 2.4  ", (
            "a token followed by retained whitespace is complete and must be "
            "kept, because the dropped bytes began after the whitespace"
        )
