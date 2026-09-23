"""How the watchdog lane contract reads a `run` block as shell commands.

Split from `test_coverage_watchdog_lane.py` because it is a reader, not a
rule: it answers which commands a run block executes unconditionally,
and which of those execute the proof script. It is deliberately not a
test module, so pytest does not collect it. Its cases live in
`test_coverage_watchdog_readers.py`; the rule that uses it lives in the
lane module.
"""

from __future__ import annotations

import re
import shlex
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The script that carries the proof. Asserted as the command the step
#: runs rather than as the step's name, because a step named "Prove the
#: cargo watchdog" that runs something else is not this rule, and a
#: differently named one that runs this script is.
PROOF_SCRIPT: typ.Final[str] = "workflow_scripts/prove_cargo_watchdog.py"


#: Words that may precede the command without making it conditional.
#: Control keywords (`if`, `then`, `do`, ...) and `!` are deliberately
#: absent: a proof behind `if false; then` never runs, and one behind `!`
#: turns a failed proof into a passing step. A command that is not
#: unconditional is not accepted as the proof.
_SHELL_PREFIXES: typ.Final[frozenset[str]] = frozenset({"time", "env"})

#: Operators that make what follows them conditional, or hand the exit
#: status to another command. `false && <proof>` never runs the proof,
#: `true || <proof>` never does either, and `<proof> | tee log` reports
#: the status of `tee` unless pipefail is set. A fragment holding any of
#: them is not an unconditional invocation.
_CONDITIONAL_OPERATORS: typ.Final[frozenset[str]] = frozenset({"&&", "||", "|", "&"})

#: A leading `NAME=value` word, which sets the command's environment
#: rather than being the command.
_ASSIGNMENT: typ.Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")

#: What may stand in the command position before the proof script. The
#: script carries a `uv run` shebang, so it may be named directly, or it
#: may be handed to `uv run`, with or without `--script`.
#:
#: An allowlist rather than a list of commands that do not execute their
#: argument. `true <proof> --runner <runner>` exits zero and runs
#: nothing, as do `echo`, `:` and every other command nobody thought to
#: list, so the command position is matched against what does run the
#: script and everything else is refused by not being here.
_LAUNCHERS: typ.Final[tuple[tuple[str, ...], ...]] = (
    (),
    ("uv", "run"),
    ("uv", "run", "--script"),
)

#: The spellings of the script's path a launcher may name.
_SCRIPT_SPELLINGS: typ.Final[frozenset[str]] = frozenset(
    {PROOF_SCRIPT, f"./{PROOF_SCRIPT}"}
)


def _tokens(fragment: str) -> list[str] | None:
    """Return *fragment*'s shell words and operators, or None if unparseable."""
    lexer = shlex.shlex(fragment, posix=True, punctuation_chars="&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        return None


#: A here-document operator and its delimiter, quoted or not. `<<-`
#: strips leading tabs from the body and the closing line; `<<<` is a
#: here-string, which has no body, and is excluded.
_HEREDOC: typ.Final[re.Pattern[str]] = re.compile(
    r"(?<!<)<<(?P<strip>-?)(?!<)\s*(?P<quote>['\"]?)(?P<delimiter>[A-Za-z0-9_]+)(?P=quote)"
)


def _without_heredoc_bodies(script: str) -> str:
    """Return *script* with every here-document body removed.

    A body is input to the command that opened it, not commands, so a
    proof invocation written inside `cat <<'EOF'` runs nothing. The line
    opening the here-document stays, since its command does run.
    """
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []
    for line in script.splitlines():
        if pending:
            delimiter, strips_tabs = pending[0]
            if (line.lstrip("\t") if strips_tabs else line) == delimiter:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(
            (match["delimiter"], match["strip"] == "-")
            for match in _HEREDOC.finditer(line)
        )
    return "\n".join(kept)


def command_lines(script: str) -> cabc.Iterator[list[str]]:
    """Yield the words of each command the run block runs unconditionally.

    Here-document bodies are dropped first, being input rather than
    commands. The block is then split on line breaks and `;`, and each
    fragment is tokenised with its operators kept. A fragment carrying a
    conditional operator yields nothing, and the words that set a
    command's environment are stripped from the front of the rest. A
    fragment starting with a control keyword is yielded as it stands, so
    its first word is the keyword and no launcher matches it.
    """
    for fragment in re.split(r"[\n;]+", _without_heredoc_bodies(script)):
        words = _tokens(fragment)
        if not words or _CONDITIONAL_OPERATORS & set(words):
            continue
        while words and (words[0] in _SHELL_PREFIXES or _ASSIGNMENT.match(words[0])):
            words = words[1:]
        if words:
            yield words


def proof_arguments(words: cabc.Sequence[str]) -> list[str] | None:
    """Return the arguments given to the proof script, if *words* runs it.

    None means the command runs something else, including a command that
    merely names the script as one of its own arguments.
    """
    for launcher in _LAUNCHERS:
        width = len(launcher)
        if tuple(words[:width]) != launcher or len(words) <= width:
            continue
        if words[width] in _SCRIPT_SPELLINGS:
            return list(words[width + 1 :])
    return None


def runner_argument(words: cabc.Sequence[str]) -> str | None:
    """Return the value the command passes to `--runner`, if any.

    Both spellings GitHub Actions authors use are read: `--runner PATH`
    and `--runner=PATH`.
    """
    for index, word in enumerate(words):
        if word == "--runner" and index + 1 < len(words):
            return words[index + 1]
        if word.startswith("--runner="):
            return word.removeprefix("--runner=")
    return None
