"""Tests for the ``detect`` helper script."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


class _FakeExit(SystemExit):
    def __init__(self, *, code: int = 0) -> None:
        super().__init__(code)
        self.code = code


_FAKE_TYPER = types.SimpleNamespace(
    Option=lambda default=None, **_: default,
    echo=lambda msg, *, err=False: print(msg, file=sys.stderr if err else sys.stdout),
    Exit=_FakeExit,
    run=lambda func: func(),
)
sys.modules.setdefault("typer", _FAKE_TYPER)

_SPEC = importlib.util.spec_from_file_location(
    "detect", Path(__file__).resolve().parents[1] / "scripts" / "detect.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
detect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(detect)


def test_invalid_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``detect.main`` exits with code 1 for an unknown format."""
    out = tmp_path / "gh.txt"
    with pytest.raises(detect.typer.Exit) as exc:
        detect.main("unknown", out)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Unsupported format" in err


@pytest.mark.parametrize(
    "fmt",
    ["lcov", "cobertura", "coveragepy", "LCOV"],
)
def test_valid_formats(
    fmt: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``detect.main`` accepts supported formats regardless of case."""
    out = tmp_path / "gh.txt"
    exc: detect.typer.Exit | None = None
    try:
        detect.main(fmt, out)
    except detect.typer.Exit as err:
        exc = err
    if fmt.lower() == "lcov":
        assert exc is not None
        assert exc.code == 1
    else:
        assert exc is None
    err_msg = capsys.readouterr().err
    assert "Unsupported format" not in err_msg
