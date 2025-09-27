#!/usr/bin/env python3
"""Convert UTF-8 plain text licence files to RTF for WiX."""

from __future__ import annotations

import argparse
import typing as typ
from pathlib import Path


def _utf16_code_units(s: str) -> typ.Iterator[int]:
    """Yield UTF-16 code units for ``s`` as signed integers."""
    data = s.encode("utf-16le")
    for i in range(0, len(data), 2):
        u = data[i] | (data[i + 1] << 8)
        yield u - 65536 if u >= 0x8000 else u


def _escape_plaintext_to_rtf(text: str) -> str:
    """Escape plain text for inclusion in an RTF body."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append(r"\\")
        elif ch == "{":
            out.append(r"\{")
        elif ch == "}":
            out.append(r"\}")
        elif ch == "\t":
            out.append(r"\tab ")
        elif ch == "\n":
            out.append(r"\par\n")
        else:
            code = ord(ch)
            if 0x20 <= code <= 0x7E:
                out.append(ch)
            else:
                out.extend(rf"\u{cu}?" for cu in _utf16_code_units(ch))
    return "".join(out)


def text_to_rtf(text: str, font: str = "Calibri", pt_size: int = 11) -> str:
    """Return an RTF document containing ``text`` rendered with ``font``."""
    fs = max(1, int(pt_size) * 2)
    header = (
        r"{\rtf1\ansi\deff0\uc1"
        rf"{{\fonttbl{{\f0 {font};}}}}"
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
    dst = Path(out_path) if out_path else src.with_suffix(".rtf")
    dst.write_text(
        text_to_rtf(src.read_text(encoding="utf-8"), font=font, pt_size=pt_size),
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
        type=int,
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
