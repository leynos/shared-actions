"""Sidecar parsing: the `<64 hex>  <name>` format `sha256sum` publishes.

Example-based cases pin the three documented outcomes - valid, malformed,
and wrong file name. The property test checks the shape of the whole
grammar: any well-formed line, however whitespace is arranged around it,
parses to the same digest, and its name field is compared exactly.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from makeutil_errors import SidecarError
from makeutil_verify import parse_sidecar

_DIGEST = "a" * 64
_NAME = "makeutil-x86_64-unknown-linux-musl"

_PROFILE = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_hex_digest = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_file_name = st.text(
    alphabet=st.characters(
        min_codepoint=0x21, max_codepoint=0x7E, exclude_characters=" \n\r"
    ),
    min_size=1,
    max_size=40,
)
_leading_trailing_blank_lines = st.lists(st.just(""), min_size=0, max_size=3)


class TestParseSidecarExamples:
    """The three outcomes the packet documents by name."""

    def test_a_valid_sidecar_returns_its_digest(self) -> None:
        """A well-formed line naming the downloaded asset parses cleanly."""
        text = f"{_DIGEST}  {_NAME}\n"

        assert parse_sidecar(text, _NAME) == _DIGEST

    def test_a_malformed_sidecar_is_refused(self) -> None:
        """A single space, not two, is not the format sha256sum publishes."""
        text = f"{_DIGEST} {_NAME}\n"

        with pytest.raises(SidecarError):
            parse_sidecar(text, _NAME)

    def test_a_sidecar_naming_a_different_file_is_refused(self) -> None:
        """A digest for the wrong asset must never be accepted as this one's."""
        text = f"{_DIGEST}  makeutil-aarch64-unknown-linux-musl\n"

        with pytest.raises(SidecarError):
            parse_sidecar(text, _NAME)

    def test_an_empty_sidecar_is_refused(self) -> None:
        """No line at all is refused the same way as a malformed one."""
        with pytest.raises(SidecarError):
            parse_sidecar("", _NAME)

    def test_a_sidecar_with_two_lines_is_refused(self) -> None:
        """Exactly one line is expected; a second is not silently ignored."""
        text = f"{_DIGEST}  {_NAME}\n{_DIGEST}  other-file\n"

        with pytest.raises(SidecarError):
            parse_sidecar(text, _NAME)


class TestParseSidecarProperty:
    """The parser's behaviour over generated whitespace and file names."""

    @given(
        digest=_hex_digest,
        name=_file_name,
        leading=_leading_trailing_blank_lines,
        trailing=_leading_trailing_blank_lines,
    )
    @_PROFILE
    def test_surrounding_blank_lines_never_change_the_parsed_digest(
        self, digest: str, name: str, leading: list[str], trailing: list[str]
    ) -> None:
        """Blank lines around a valid one are noise, not a second record."""
        body_lines = [*leading, f"{digest}  {name}", *trailing]
        text = "\n".join(body_lines) + "\n"

        assert parse_sidecar(text, name) == digest

    @given(digest=_hex_digest, name=_file_name, other_name=_file_name)
    @_PROFILE
    def test_a_name_mismatch_is_always_refused(
        self, digest: str, name: str, other_name: str
    ) -> None:
        """Whatever the two names are, disagreeing ones are never accepted."""
        assume(name != other_name)
        text = f"{digest}  {name}\n"

        with pytest.raises(SidecarError):
            parse_sidecar(text, other_name)
