"""The parser proof's verdicts, without the service.

``prove_parser.py`` decides from ``cs-coverage check``'s exit status and
output whether the pinned CLI parsed a fixture. The verdict is driven here on
chosen results, and ``main``'s two refusals against a stand-in on ``PATH``.
"""

from __future__ import annotations

import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prove_parser.py"
_SPEC = importlib.util.spec_from_file_location("prove_parser", SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
prove_parser = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = prove_parser
_SPEC.loader.exec_module(prove_parser)

#: The repository root, whose fixtures ``main`` proves.
REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[4]


class TestVerdict:
    """Each way a run fails the proof, and the one way it passes."""

    @pytest.mark.parametrize(
        ("status", "output", "expected"),
        [
            pytest.param(0, "Code coverage gates: PASS", None, id="pass"),
            pytest.param(
                0,
                f"{prove_parser.KNOWN_PARSER_FAILURE}\nCode coverage gates: PASS",
                "cs-coverage 1.0.101 reported the known parser failure",
                id="parser-break-with-status-zero",
            ),
            pytest.param(
                3,
                prove_parser.KNOWN_PARSER_FAILURE,
                "cs-coverage 1.0.101 reported the known parser failure",
                id="parser-break-with-failure",
            ),
            pytest.param(2, "", "cs-coverage exited 2", id="non-zero"),
            pytest.param(
                0,
                "Code coverage gates: FAIL",
                "cs-coverage did not report a PASS result",
                id="no-pass",
            ),
        ],
    )
    def test_the_verdict(self, status: int, output: str, expected: str | None) -> None:
        """The known break fails the proof even when the CLI exits zero."""
        assert prove_parser.verdict(status, output) == expected


def _stand_in_cli(directory: Path, *, output: str, status: int) -> None:
    """Put a ``cs-coverage`` on *directory* that prints *output* and exits."""
    cli = directory / "cs-coverage"
    cli.write_text(
        f"#!/usr/bin/env sh\nprintf '%s\\n' '{output}'\nexit {status}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is POSIX sh")
class TestMain:
    """What ``main`` refuses before running anything.

    The step's end-to-end behaviour against a stand-in CLI, including the
    exit status it preserves, is driven through the workflow's own ``run:``
    in ``test_cold_runner_parser.py``.
    """

    def test_a_missing_credential_fails_before_any_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the token the proof proves nothing, so it fails first."""
        _stand_in_cli(tmp_path, output="Code coverage gates: PASS", status=0)
        monkeypatch.setenv("PATH", f"{tmp_path}:{Path('/usr/bin')}:{Path('/bin')}")
        monkeypatch.delenv("CS_ACCESS_TOKEN", raising=False)

        assert prove_parser.main(REPOSITORY_ROOT) == 1

    def test_no_fixtures_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A proof over no fixtures would pass by looping over nothing."""
        monkeypatch.setenv("CS_ACCESS_TOKEN", "stand-in")

        assert prove_parser.main(tmp_path) == 1
