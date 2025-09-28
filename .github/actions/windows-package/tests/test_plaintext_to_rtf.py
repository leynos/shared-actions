"""Tests for the plaintext-to-RTF conversion helper."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

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
    assert header.startswith(
        "{\\rtf1\\ansi\\deff0\\uc1{\\fonttbl{\\f0 Segoe UI Emoji;}}\\f0\\fs22\\pard"
    )

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


def test_text_to_rtf_with_empty_string() -> None:
    """Empty input should yield a valid RTF document with no body content."""
    rtf = SCRIPT.text_to_rtf("")

    assert rtf.startswith(
        "{\\rtf1\\ansi\\deff0\\uc1{\\fonttbl{\\f0 Calibri;}}\\f0\\fs22\\pard"
    )
    assert rtf.endswith("\n}")


def test_text_to_rtf_only_control_characters() -> None:
    """Control characters are escaped or normalised in the body."""
    rtf = SCRIPT.text_to_rtf("\t\r\n\n")

    header, body = rtf.split("\n", 1)
    assert header.endswith("\\pard ")
    assert body.startswith("\\tab ")
    assert body.count("\\par\\n") == 2
    assert body.endswith("}")


def test_text_to_rtf_strips_utf8_bom() -> None:
    """Leading BOM code units should not appear in the escaped RTF body."""
    rtf = SCRIPT.text_to_rtf("\ufeffHello")

    assert "\\u65279?" not in rtf
    assert "Hello" in rtf


def test_text_to_rtf_header_escapes_font_name() -> None:
    """Font names with control characters are escaped inside the header."""
    font = r"Foo {Bar}\Baz"
    rtf = SCRIPT.text_to_rtf("X", font=font)

    header, _ = rtf.split("\n", 1)
    assert r"{\fonttbl{\f0 Foo \{Bar\}" in header
    assert r"\\Baz;}}" in header


def test_convert_file_respects_explicit_output_path(tmp_path: Path) -> None:
    """Conversion writes to the provided destination when supplied."""
    source = tmp_path / "LICENSE.txt"
    destination = tmp_path / "out" / "LICENCE_COPY.rtf"
    destination.parent.mkdir()
    source.write_text("a\r\nb\nc\rd", encoding="utf-8")

    output = SCRIPT.convert_file(str(source), str(destination), font="Arial", pt_size=9)

    assert output == destination
    assert destination.exists()
    assert not (tmp_path / "LICENSE.rtf").exists()

    rendered = destination.read_text(encoding="utf-8")
    assert "\\par\\n" in rendered
    assert "\r" not in rendered


def test_convert_file_without_suffix_adds_rtf(tmp_path: Path) -> None:
    """Files without a suffix gain `.rtf` rather than raising ``ValueError``."""
    source = tmp_path / "LICENSE"
    source.write_text("Plain text", encoding="utf-8")

    output = SCRIPT.convert_file(str(source))

    assert output == tmp_path / "LICENSE.rtf"
    assert output.exists()


def test_convert_file_missing_input_raises() -> None:
    """Missing input files should raise ``FileNotFoundError`` from read_text."""
    with pytest.raises(FileNotFoundError):
        SCRIPT.convert_file("/path/that/does/not/exist.txt")


def test_parse_args_with_explicit_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI parser should surface input and output arguments unchanged."""
    fake_input = tmp_path / "input.txt"
    argv = ["plaintext_to_rtf.py", "--input", str(fake_input), "--output", "custom.rtf"]
    monkeypatch.setattr(sys, "argv", argv)

    args = SCRIPT.parse_args()

    assert args.input == str(fake_input)
    assert args.output == "custom.rtf"


def test_main_converts_with_explicit_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI ``main`` should honour the provided output path and print it."""
    source = tmp_path / "input.txt"
    source.write_text("cli text", encoding="utf-8")
    destination = tmp_path / "dest" / "cli.rtf"
    destination.parent.mkdir()

    argv = [
        "plaintext_to_rtf.py",
        "--input",
        str(source),
        "--output",
        str(destination),
        "--font",
        "Courier New",
        "--point-size",
        "14",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    SCRIPT.main()
    captured = capsys.readouterr()

    assert captured.out.strip() == str(destination)
    assert destination.exists()
    assert "cli text" in destination.read_text(encoding="utf-8")
