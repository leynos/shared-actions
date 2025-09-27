"""Tests for the plaintext-to-RTF conversion helper."""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "plaintext_to_rtf.py"


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "windows_package_plaintext_to_rtf",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive guard
        msg = f"cannot load module from {MODULE_PATH}"
        raise RuntimeError(msg)  # pragma: no cover - defensive guard
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, types.ModuleType)
    return module


SCRIPT = _load_script()


def test_text_to_rtf_renders_unicode_and_control_sequences() -> None:
    """Render mixed ASCII, Unicode, and control characters into WiX-compatible RTF."""
    text = (
        "naïve — snowman ☃ and emoji 🤖\r\nTabbed\tline with braces {} and backslash \\"
    )

    rtf = SCRIPT.text_to_rtf(text, font="Segoe UI Emoji", pt_size=11)

    header, body_with_tail = rtf.split("\n", 1)
    expected_header = (
        "{\\rtf1\\ansi\\deff0\\uc1{\\fonttbl{\\f0 Segoe UI Emoji;}}\\f0\\fs22\\pard "
    )
    assert header == expected_header

    body = body_with_tail.rstrip("}")
    assert "na\\u239?ve" in body  # ï encoded as UTF-16 code unit
    assert "\\u8212?" in body  # em dash encoded as Unicode escape
    assert "\\u9731?" in body  # snowman symbol
    assert "\\u-10178?\\u-8938?" in body  # surrogate pair for the robot emoji
    assert "\\par\\nTabbed" in body  # newline converted to RTF paragraph break
    assert "\\tab line" in body  # tab converted to \tab control word
    assert "\\{\\}" in body  # curly braces escaped
    assert " \\\\" in body  # literal backslash escaped


def test_convert_file_creates_rtf_sibling(tmp_path: Path) -> None:
    """Convert plaintext input to a sibling .rtf when output path is omitted."""
    source = tmp_path / "LICENSE.txt"
    source.write_text("Line one\rLine two", encoding="utf-8")

    output = SCRIPT.convert_file(str(source), font="Consolas", pt_size=10)

    assert output == tmp_path / "LICENSE.rtf"

    rendered = output.read_text(encoding="utf-8")
    assert rendered.startswith(
        "{\\rtf1\\ansi\\deff0\\uc1{\\fonttbl{\\f0 Consolas;}}\\f0\\fs20\\pard "
    )
    assert "Line one" in rendered
    assert "\\par\\nLine two" in rendered
    assert rendered.endswith("}")
    assert "\r" not in rendered  # carriage returns normalised to newlines
