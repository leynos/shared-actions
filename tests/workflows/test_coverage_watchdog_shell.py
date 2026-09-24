"""Tests for where the watchdog lane contract finds a run block's commands.

`_watchdog_command_reading` splits a run block into commands the way bash
does, and reads a proof step's block for its only command. These cases
pin both readings: a separator inside quotes, an escape or a comment
separates nothing, and a block holding anything beside the proof is not
the proof. Kept apart from `test_coverage_watchdog_readers.py` so each
module stays small enough to review.
"""

from __future__ import annotations

import typing as typ

import pytest

from . import _watchdog_command_reading as shell

#: The proof invocation the cases place in and around other text.
_PROOF_LINE: typ.Final[str] = f"uv run --script {shell.PROOF_SCRIPT} --runner R"


class TestCommandSeparators:
    """Where a run block's commands begin and end, as bash reads them."""

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            pytest.param(f"printf 'x; {_PROOF_LINE}; y'", False, id="single-quoted"),
            pytest.param(f'echo "x; {_PROOF_LINE}"', False, id="double-quoted"),
            pytest.param(f"echo x \\; {_PROOF_LINE}", False, id="escaped-semicolon"),
            pytest.param(f"echo x # ; {_PROOF_LINE}", False, id="in-a-comment"),
            pytest.param(f"false &&\n{_PROOF_LINE}", False, id="and-at-line-end"),
            pytest.param(f"true ||\n{_PROOF_LINE}", False, id="or-at-line-end"),
            pytest.param(f"echo 'x; {_PROOF_LINE}", False, id="unterminated-quote"),
            pytest.param(f"echo 'x'; {_PROOF_LINE}", True, id="after-a-quoted-command"),
            pytest.param(f"echo x#y; {_PROOF_LINE}", True, id="hash-inside-a-word"),
            pytest.param(f"{_PROOF_LINE} # why", True, id="trailing-comment"),
        ],
    )
    def test_only_an_unquoted_separator_starts_a_command(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """A `;` or line break inside quotes, an escape or a comment is text.

        Split naively, `printf 'x; <proof>; y'` exposes the proof as a
        command of its own, and the rule passes while the step prints a
        string. A line ending in `&&` or `||` continues into the next, so
        the proof on that line is still conditional.
        """
        arguments = [
            found
            for words in shell.command_lines(script)
            if (found := shell.proof_arguments(words)) is not None
        ]

        assert arguments == ([["--runner", "R"]] if expected else []), (
            f"{script!r} should {'' if expected else 'not '}count as executing "
            f"the proof; read as {arguments!r}"
        )


class TestTheSoleCommand:
    """A proof step's block is the proof and nothing else."""

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            pytest.param(_PROOF_LINE, True, id="alone"),
            pytest.param(f"{_PROOF_LINE}\n", True, id="trailing-newline"),
            pytest.param(f"{_PROOF_LINE}\n# why\n", True, id="with-a-comment-line"),
            pytest.param(
                f"set +e\n{_PROOF_LINE}\ntrue\n", False, id="set-e-off-then-true"
            ),
            pytest.param(f"{_PROOF_LINE}; true", False, id="followed-by-true"),
            pytest.param(
                f"echo start\n{_PROOF_LINE}", False, id="after-another-command"
            ),
            pytest.param(f"false && {_PROOF_LINE}", False, id="conditional"),
            pytest.param(
                f"true && set +e\n{_PROOF_LINE}\ntrue || true",
                False,
                id="beside-conditional-commands",
            ),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_only_a_block_holding_the_proof_alone_counts(
        self,
        script: str,
        expected: bool,  # noqa: FBT001 - boolean literals clarify parametrized cases.
    ) -> None:
        """Another command in the block can decide the step's exit status.

        `set +e`, the proof, then `true` runs the proof and reports success
        whatever it found, so the lane rule reads the block's only command.
        """
        words = shell.sole_command(script)
        arguments = None if words is None else shell.proof_arguments(words)

        assert (arguments == ["--runner", "R"]) is expected, (
            f"{script!r} should {'' if expected else 'not '}count as the "
            f"proof alone; read as {words!r}"
        )
