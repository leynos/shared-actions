"""How the watchdog lane contract reads a `run` block as shell commands.

Split from `test_coverage_watchdog_lane.py` because it is a reader, not a
rule: it answers which commands a run block executes unconditionally,
whether the block holds one command alone, and which commands execute
the proof script. It is deliberately not a test module, so pytest does
not collect it. Its cases live in `test_coverage_watchdog_readers.py` and
`test_coverage_watchdog_shell.py`; the rule that uses it lives in the
lane module.
"""

from __future__ import annotations

import itertools
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


#: One lexical unit of a run block, as bash reads separators. A quoted
#: string and an escaped character are text whatever they contain, so a
#: `;` inside `printf 'x; y'` separates nothing. A `#` opens a comment
#: only at the start of a word, as it does in bash; `a#b` is one word.
#: An unterminated quote swallows the rest of the block, which then fails
#: to tokenise and so is never read as a command.
_LEXEME: typ.Final[re.Pattern[str]] = re.compile(
    r"""
    (?P<quoted>'[^']*'|"(?:\\.|[^"\\])*")
    |(?P<escaped>\\.)
    |(?P<comment>(?:(?<=[\s;&|()<>])|\A)\#[^\n]*)
    |(?P<separator>[;\n])
    |(?P<unterminated>['"].*)
    |(?P<text>[^'"\\;\n\#]+|\#|\\\Z)
    """,
    re.VERBOSE | re.DOTALL,
)

#: Operators after which bash reads the next line as the rest of the
#: same command list. `false &&` on one line and the proof on the next is
#: one conditional command, not two commands.
_CONTINUING_OPERATORS: typ.Final[tuple[str, ...]] = ("&&", "|")


def _continues(fragment: str, separator: str) -> bool:
    """Return whether *separator* leaves *fragment*'s command unfinished."""
    return separator == "\n" and fragment.rstrip().endswith(_CONTINUING_OPERATORS)


def _fragments(script: str) -> list[str]:
    """Return *script*'s commands as text, split where bash separates them.

    Only an unquoted, unescaped `;` or line break outside a comment
    separates commands. Comments are dropped, an escaped line break joins
    its two lines, and a line ending in `&&`, `||` or `|` runs on into the
    next. Blank fragments are kept; the callers skip them.
    """
    fragments = [""]
    for lexeme in _LEXEME.finditer(_without_heredoc_bodies(script)):
        kind, text = lexeme.lastgroup, lexeme.group()
        if kind == "separator":
            if _continues(fragments[-1], text):
                fragments[-1] += " "
            else:
                fragments.append("")
        elif kind != "comment" and text != "\\\n":
            fragments[-1] += text
    return fragments


def _is_prefix(word: str) -> bool:
    """Return whether *word* may precede a command without being it."""
    return word in _SHELL_PREFIXES or _ASSIGNMENT.match(word) is not None


def _unconditional(words: list[str]) -> list[str] | None:
    """Return a command's words without its prefixes, or None if conditional."""
    if _CONDITIONAL_OPERATORS & set(words):
        return None
    return list(itertools.dropwhile(_is_prefix, words)) or None


def command_lines(script: str) -> cabc.Iterator[list[str]]:
    """Yield the words of each command the run block runs unconditionally.

    Here-document bodies are dropped first, being input rather than
    commands. The block is then split where bash separates commands (see
    `_fragments`), and each fragment is tokenised with its operators kept.
    A fragment carrying a conditional operator yields nothing, and the
    words that set a command's environment are stripped from the front of
    the rest. A fragment starting with a control keyword is yielded as it
    stands, so its first word is the keyword and no launcher matches it.

    Parameters
    ----------
    script : str
        The step's `run` block, as the workflow declares it.

    Yields
    ------
    list of str
        The words of one unconditional command, command word first.

    Examples
    --------
    >>> list(command_lines("echo 'a; b'; RUST_LOG=1 cargo test"))
    [['echo', 'a; b'], ['cargo', 'test']]
    """
    for fragment in _fragments(script):
        words = _tokens(fragment)
        if words and (command := _unconditional(words)) is not None:
            yield command


def sole_command(script: str) -> list[str] | None:
    """Return the words of the run block's only command, if it has one.

    A step's exit status is its last command's, and only while errexit
    holds. `set +e`, then a failing command, then `true`, gives a green
    step. So the proof must be the whole block: one command, unconditional,
    whose status is the step's. A block holding anything else, even a
    harmless `echo`, is refused rather than read for whether it could
    mask a failure, because listing the commands that can is the
    denylist this reader avoids.

    Parameters
    ----------
    script : str
        The step's `run` block, as the workflow declares it.

    Returns
    -------
    list of str or None
        The command's words, or None if the block holds no command, more
        than one, a conditional one, or text that does not tokenise.

    Examples
    --------
    >>> sole_command("uv run tool.py --flag")
    ['uv', 'run', 'tool.py', '--flag']
    >>> sole_command("set +e; uv run tool.py; true") is None
    True
    """
    present = [fragment for fragment in _fragments(script) if _tokens(fragment) != []]
    commands = list(command_lines(script))
    return commands[0] if len(present) == 1 and len(commands) == 1 else None


def proof_arguments(words: cabc.Sequence[str]) -> list[str] | None:
    """Return the arguments given to the proof script, if *words* runs it.

    Parameters
    ----------
    words : sequence of str
        One command's words, as `command_lines` or `sole_command` yields
        them.

    Returns
    -------
    list of str or None
        The words after the script's path, or None when the command runs
        something else, including a command that merely names the script
        as one of its own arguments.

    Examples
    --------
    >>> proof_arguments(["uv", "run", PROOF_SCRIPT, "--runner", "R"])
    ['--runner', 'R']
    >>> proof_arguments(["echo", PROOF_SCRIPT]) is None
    True
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

    Parameters
    ----------
    words : sequence of str
        The arguments given to the proof script.

    Returns
    -------
    str or None
        The runner's path, or None when no `--runner` carries a value.

    Examples
    --------
    >>> runner_argument(["--runner=run_rust.py"])
    'run_rust.py'
    >>> runner_argument(["--runner"]) is None
    True
    """
    for index, word in enumerate(words):
        if word == "--runner" and index + 1 < len(words):
            return words[index + 1]
        if word.startswith("--runner="):
            return word.removeprefix("--runner=")
    return None
