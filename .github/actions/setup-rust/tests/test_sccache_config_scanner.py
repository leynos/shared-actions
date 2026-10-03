"""How the start step reads a caller's TOML config for its startup timeout.

The step decides with a small `awk` scanner whether the caller already chose a
root `server_startup_timeout_ms`. The cases here hold what that scanner must and
must not treat as the root key: a name inside a multi-line string or after a
comment delimiter, content quotes before a closing delimiter, and which branch
decided, reported as a metric. The wider contract of the start step lives in
`test_sccache_server_start.py`, and the generated documents in
`test_sccache_startup_config_properties.py`.
"""

from __future__ import annotations

import tomllib
import typing as typ

import pytest
import test_sccache_server_start as start
from test_sccache_server_start import Scenario, _run_server, _written_conf

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

#: The stub-sccache fixture of the start step's own tests, shared by name.
fake_sccache = start.fake_sccache


class TestConfigScanner:
    """The scanner's reading of a caller's config."""

    @pytest.mark.parametrize("delimiter", ['"""', "'''"])
    def test_a_timeout_named_inside_a_multiline_string_is_not_the_root_key(
        self, fake_sccache: Path, delimiter: str
    ) -> None:
        """TOML treats those lines as string content, not as a root setting.

        Scanning line by line would take the string's content for the caller's
        own timeout and skip the required default.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        text = f"alpha = {delimiter}\nserver_startup_timeout_ms = 123\n{delimiter}\n"
        theirs.write_text(text, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == "server_startup_timeout_ms = 60000\n" + text

    def test_an_escaped_delimiter_inside_a_multiline_string_does_not_close_it(
        self, fake_sccache: Path
    ) -> None:
        """A backslash before a triple quote keeps the string open.

        In a multi-line basic string a backslash then a quote is an escaped
        quote, so a backslash followed by three quotes is that quote plus two
        content quotes, not a closing delimiter. A scanner that closed the
        string there would take the timeout-like content line after it for the
        caller's own root key, skip the required default and leave the file
        without the 60 s timeout.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        text = (
            'alpha = """\n'
            'say \\""" and carry on\n'
            "server_startup_timeout_ms = 123\n"
            '"""\n'
        )
        parsed = tomllib.loads(text)
        assert "server_startup_timeout_ms" not in parsed, (
            "the timeout-like line is string content, not a root key"
        )
        assert 'say """ and carry on' in parsed["alpha"]
        theirs.write_text(text, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == "server_startup_timeout_ms = 60000\n" + text

    @pytest.mark.parametrize(
        ("preamble", "case"),
        [
            ('# Example delimiter: """', "a comment naming a basic delimiter"),
            ("# Example delimiter: '''", "a comment naming a literal delimiter"),
            ('alpha = "a # b"', "a string holding a hash"),
            ('alpha = "say \\"\\"\\""', "a basic string with escaped quotes"),
        ],
    )
    def test_a_delimiter_outside_a_multiline_string_opens_nothing(
        self, fake_sccache: Path, preamble: str, case: str
    ) -> None:
        """Only a real opener starts a multi-line string.

        A delimiter inside a comment or a one-line string is not one, and
        treating it as one would swallow the caller's own timeout and prepend a
        duplicate key, which makes the file invalid TOML.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        text = f"{preamble}\nserver_startup_timeout_ms = 5000\n"
        assert tomllib.loads(text)["server_startup_timeout_ms"] == 5000, case
        theirs.write_text(text, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == text, case

    @pytest.mark.parametrize(
        "array",
        [
            'alpha = ["""x"""", """\ntext\n"""]',
            "alpha = ['''x'''', '''\ntext\n''']",
            'alpha = ["""x""""", """y"""]',
        ],
        ids=["basic-four-quotes", "literal-four-quotes", "basic-five-quotes"],
    )
    def test_content_quotes_before_a_closing_delimiter_are_consumed(
        self, fake_sccache: Path, array: str
    ) -> None:
        """TOML allows one or two content quotes just before a closing delimiter.

        A run of four or five quotes closes the string at its end. Consuming
        only the first three leaves a stray quote that opens a one-line string
        over the next opener, so the scanner would take that opener's closing
        delimiter for a new opener and skip the caller's own timeout, which
        then gets a duplicate prepended and the file stops being valid TOML.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        text = f"{array}\nserver_startup_timeout_ms = 5000\n"
        assert tomllib.loads(text)["server_startup_timeout_ms"] == 5000
        theirs.write_text(text, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == text

    @pytest.mark.parametrize(
        ("caller_config", "expected"),
        [
            (None, "default"),
            ('[cache.gha]\nversion = "x"\n', "merged"),
            ("server_startup_timeout_ms = 5000\n", "caller"),
        ],
        ids=["no-config", "caller-config-without-timeout", "caller-timeout"],
    )
    def test_the_timeout_decision_is_reported(
        self, fake_sccache: Path, caller_config: str | None, expected: str
    ) -> None:
        """Which branch chose the startup timeout is visible in the log."""
        workdir = fake_sccache.parent
        theirs = None
        if caller_config is not None:
            theirs = workdir / "theirs.toml"
            theirs.write_text(caller_config, encoding="utf-8")
        completed = _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs) if theirs else None,
            )
        )

        assert f"metric setup-rust.sccache.timeout={expected}" in (
            completed.stdout.splitlines()
        )
