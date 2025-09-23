"""Tests for the macOS platform detection helper."""

from __future__ import annotations

import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from collections import abc as cabc
else:  # pragma: no cover - runtime fallback for annotations
    cabc = typ.cast("object", None)


def test_main_passes_on_macos(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Succeed silently when the host platform reports macOS."""
    module = load_module("check_platform")
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")

    module.main()

    captured = capsys.readouterr()
    assert "macOS runner detected" in captured.out
    assert captured.err == ""


def test_main_exits_on_non_macos(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_module: cabc.Callable[[str], object],
) -> None:
    """Exit with code ``1`` when the platform is not macOS."""
    module = load_module("check_platform")
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 1
    assert "macOS runner" in capsys.readouterr().err
