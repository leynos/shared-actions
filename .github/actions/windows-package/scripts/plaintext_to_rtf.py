#!/usr/bin/env python3
"""Convert UTF-8 plain text licence files to RTF for WiX."""

from __future__ import annotations

import argparse
import struct
import typing as typ
from pathlib import Path


def _positive_int(value: str) -> int:
    """Return ``value`` as an integer ensuring it is positive."""
    point_size = int(value)
    if point_size < 1:
        message = "point size must be >= 1"
        raise argparse.ArgumentTypeError(message)
    return point_size


def _utf16_code_units(s: str) -> typ.Iterator[int]:
    """Yield UTF-16 code units for ``s`` as signed integers."""
    data = s.encode("utf-16le")
    for (cu,) in struct.iter_unpack("<H", data):
        yield cu - 0x10000 if cu >= 0x8000 else cu


_ESCAPE_RTF = {
    "\\": r"\\",
    "{": r"\{",
    "}": r"\}",
    "\t": r"\tab ",
    "\n": r"\par\n",
}


def _escape_plaintext_to_rtf(text: str) -> str:
    """Escape plain text for inclusion in an RTF body."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.removeprefix("\ufeff")
    out: list[str] = []
    for ch in text:
        if esc := _ESCAPE_RTF.get(ch):
            out.append(esc)
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            out.extend(rf"\u{cu}?" for cu in _utf16_code_units(ch))
    return "".join(out)


def _escape_font_name(font: str) -> str:
    """Return ``font`` escaped for inclusion inside the font table."""
    return font.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def text_to_rtf(text: str, font: str = "Calibri", pt_size: int = 11) -> str:
    """Return an RTF document containing ``text`` rendered with ``font``."""
    fs = max(1, pt_size * 2)
    header = (
        r"{\rtf1\ansi\deff0\uc1"
        rf"{{\fonttbl{{\f0 {_escape_font_name(font)};}}}}"
        rf"\f0\fs{fs}\pard "
        "\n"
    )
    body = _escape_plaintext_to_rtf(text)
    return header + body + "}"


def convert_file(
    in_path: str,
    out_path: str | None = None,
    *,
    font: str = "Calibri",
    pt_size: int = 11,
) -> Path:
    """Convert ``in_path`` to RTF, returning the written destination path."""
    src = Path(in_path)
    if out_path:
        dst = Path(out_path)
    elif src.suffix:
        dst = src.with_suffix(".rtf")
    else:
        dst = src.with_name(f"{src.name}.rtf")
    dst.write_text(
        text_to_rtf(
            src.read_text(encoding="utf-8-sig"),
            font=font,
            pt_size=pt_size,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return dst


def parse_args() -> argparse.Namespace:
    """Return parsed command-line arguments for the converter CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, help="UTF-8 plain text licence file to convert."
    )
    parser.add_argument(
        "--output",
        help="Destination RTF path. Defaults to replacing the input suffix with .rtf.",
    )
    parser.add_argument(
        "--font", default="Calibri", help="Font name embedded in the RTF header."
    )
    parser.add_argument(
        "--point-size",
        type=_positive_int,
        default=11,
        help="Font point size for the generated RTF (integer, default: 11).",
    )
    return parser.parse_args()


def main() -> None:
    """Invoke :func:`convert_file` and print the generated path."""
    args = parse_args()
    output = convert_file(
        args.input, args.output, font=args.font, pt_size=args.point_size
    )
    print(output)


if __name__ == "__main__":
    main()
