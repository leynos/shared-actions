"""Contract for the `skylos-allow` exception workflow.

Acquiring one documented exception crosses a shell recipe, a Python lock
helper, and the Skylos CLI. These tests drive the real target with a
recorder standing in for Skylos, so argument forwarding and lock
behaviour are observed rather than asserted from source.
"""

from __future__ import annotations

import json
import sys
import tomllib
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import example, given, settings

from workflow_scripts import skylos_allow
from workflow_scripts.tests.skylos_contract_support import (
    _BLANK_TEXT,
    _REPOSITORY_ROOT,
    _SHELL_ARGUMENT_TEXT,
    _SKYLOS_WHITELIST_LOCK_TOKENS,
    _SKYLOS_WHITELIST_TOKENS,
    _UV_SHELL_TOKENS,
    _WILDCARD_SYMBOLS,
    _isolated_skylos_allow_arguments,
    _make_command,
    _python_recorder_cli,
    _recipe_tokens,
    _skylos_allow_environment,
    _uv_passthrough_launcher,
    _variable_tokens,
    _WhitelistRunner,
)

if typ.TYPE_CHECKING:
    import pytest


class TestSkylosAllowContract:
    """Exceptions must be validated, forwarded exactly, and locked."""

    def test_skylos_allow_recipe_dispatches_the_whitelist_subcommand_first(
        self,
    ) -> None:
        """The exception target must not place scan options before ``whitelist``."""
        assert _variable_tokens("UV_SHELL") == _UV_SHELL_TOKENS, (
            "the skylos-allow launcher must normalize Windows executable paths "
            "for the POSIX shell"
        )
        assert (
            _variable_tokens("SKYLOS_WHITELIST_LOCK") == _SKYLOS_WHITELIST_LOCK_TOKENS
        ), "Skylos whitelist updates must use the repository-local lock path"
        commands = [
            command
            for command in _recipe_tokens("skylos-allow")
            if command[:4] == _SKYLOS_WHITELIST_TOKENS[:4]
        ]
        assert commands == [_SKYLOS_WHITELIST_TOKENS], (
            "skylos-allow must lock, then dispatch whitelist before the symbol "
            "and --reason"
        )

    @settings(max_examples=25, deadline=None)
    @given(value=_BLANK_TEXT)
    def test_skylos_allow_rejects_missing_or_whitespace_values(
        self, value: str
    ) -> None:
        """Missing and whitespace-only exception values must exit two without writes."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        requests = (
            ({}, "SYMBOL"),
            ({"SYMBOL": "handler"}, "REASON"),
            ({"SYMBOL": value, "REASON": "verified caller"}, "SYMBOL"),
            ({"SYMBOL": "handler", "REASON": value}, "REASON"),
        )
        for values, missing_name in requests:
            returncode, _stdout, stderr = _make_command(
                "skylos-allow", environment=_skylos_allow_environment(**values)
            )
            assert returncode == 2, (
                f"skylos-allow must reject missing or whitespace-only {missing_name}"
            )
            assert (
                f"Error: {missing_name} is required for a named whitelist exception"
                in stderr
            ), f"skylos-allow must name the missing {missing_name} validation error"
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "invalid skylos-allow requests must not mutate pyproject.toml"
        )

    def test_skylos_allow_rejects_wildcard_symbols(self) -> None:
        """Wildcard-bearing symbols must not reach the Skylos whitelist command."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        for wildcard in _WILDCARD_SYMBOLS:
            returncode, _stdout, stderr = _make_command(
                "skylos-allow",
                environment=_skylos_allow_environment(
                    SYMBOL=f"registered{wildcard}handler",
                    REASON="verified runtime caller",
                ),
            )
            assert returncode == 2, "skylos-allow must reject wildcard-bearing symbols"
            assert (
                "Error: SYMBOL must not contain wildcard characters (*, ?, or [)"
                in stderr
            ), "skylos-allow must explain its wildcard-symbol rejection"
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "wildcard-symbol validation must not mutate pyproject.toml"
        )

    @settings(max_examples=25, deadline=None)
    @example(symbol="$(handler);!", reason='Loaded "$plugin" | registry')
    @given(symbol=_SHELL_ARGUMENT_TEXT, reason=_SHELL_ARGUMENT_TEXT)
    def test_skylos_allow_forwards_generated_arguments_exactly(
        self, symbol: str, reason: str
    ) -> None:
        """A recorder must receive every valid symbol and reason as one argument."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            recorded_arguments = directory / "arguments.json"
            recorder = directory / "skylos_recorder.py"
            recorder.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n\n"
                'Path(os.environ["SKYLOS_ARGUMENTS_PATH"]).write_text(\n'
                "    json.dumps(sys.argv[1:]), encoding='utf-8'\n"
                ")\n",
                encoding="utf-8",
            )
            recorder.chmod(0o755)
            uv_launcher = _uv_passthrough_launcher(directory)
            environment = _skylos_allow_environment(
                SKYLOS_ARGUMENTS_PATH=str(recorded_arguments),
                SYMBOL=symbol,
                REASON=reason,
            )
            returncode, _stdout, stderr = _make_command(
                *_isolated_skylos_allow_arguments(
                    directory,
                    skylos_cli=_python_recorder_cli("skylos_recorder"),
                    uv_launcher=uv_launcher,
                ),
                environment=environment,
                working_directory=directory,
            )
            assert returncode == 0, (
                f"skylos-allow must forward valid generated arguments: {stderr}"
            )
            assert recorded_arguments.is_file(), (
                "the temporary Skylos recorder must execute on each supported host; "
                f"stdout={_stdout!r}, stderr={stderr!r}"
            )
            assert json.loads(recorded_arguments.read_text(encoding="utf-8")) == [
                "whitelist",
                symbol,
                "--reason",
                reason,
            ], "Skylos must receive each generated value as exactly one argument"
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "recorder-backed skylos-allow requests must not mutate pyproject.toml"
        )

    def test_skylos_allow_lock_preserves_concurrent_documented_entries(
        self,
    ) -> None:
        """The whitelist lock must prevent concurrent documented-entry loss."""
        pyproject_before = (_REPOSITORY_ROOT / "pyproject.toml").read_bytes()
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "pyproject.toml").write_text(
                "[tool.skylos.whitelist.documented]\n", encoding="utf-8"
            )
            writer = directory / "skylos_whitelist_writer.py"
            writer.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                "import sys\n"
                "import time\n"
                "symbol = sys.argv[2]\n"
                "reason = sys.argv[4]\n"
                "path = Path('pyproject.toml')\n"
                "contents = path.read_text(encoding='utf-8')\n"
                "time.sleep(0.2)\n"
                "path.write_text(contents + f'{symbol} = {reason!r}\\n', "
                "encoding='utf-8')\n",
                encoding="utf-8",
            )
            writer.chmod(0o755)
            runner = _WhitelistRunner(
                directory=directory,
                skylos_cli=_python_recorder_cli("skylos_whitelist_writer"),
                uv_launcher=_uv_passthrough_launcher(directory),
            )
            first = runner.start("first", "first reason")
            second = runner.start("second", "second reason")
            first_stdout, first_stderr = first.communicate()
            second_stdout, second_stderr = second.communicate()

            assert first.returncode == 0, (
                "the first Skylos whitelist update must succeed: "
                f"{first_stdout}{first_stderr}"
            )
            assert second.returncode == 0, (
                "the second Skylos whitelist update must succeed: "
                f"{second_stdout}{second_stderr}"
            )
            with (directory / "pyproject.toml").open("rb") as configuration_file:
                configuration = tomllib.load(configuration_file)
            documented = typ.cast(
                "dict[str, object]",
                configuration["tool"]["skylos"]["whitelist"]["documented"],
            )
        assert documented == {"first": "first reason", "second": "second reason"}, (
            "Skylos whitelist locking must preserve every concurrent documented entry"
        )
        assert (_REPOSITORY_ROOT / "pyproject.toml").read_bytes() == pyproject_before, (
            "isolated concurrent Skylos whitelist tests must not mutate pyproject.toml"
        )

    def test_missing_skylos_executable_reports_status_127(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing configured Skylos executable must return a useful diagnostic."""
        missing_command = f"{tmp_path}/missing-skylos-cli"
        result = skylos_allow.main(
            ["--lock-file", str(tmp_path / "skylos.lock"), "--", missing_command]
        )

        assert result == 127, (
            "a missing Skylos CLI must return command-not-found status"
        )
        assert "cannot execute" in capsys.readouterr().err, (
            "a missing Skylos CLI must identify the failed command"
        )
