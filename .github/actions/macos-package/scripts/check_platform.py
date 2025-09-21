#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# ///

"""Ensure the action is executed on a macOS runner."""

from __future__ import annotations

import platform
import sys


def main() -> None:
    """Exit with a non-zero status when the host is not macOS."""
    system = platform.system()
    if system != "Darwin":
        sys.stderr.write(
            f"This action must run on a macOS runner. Detected platform: {system}\n",
        )
        raise SystemExit(1)

    print("macOS runner detected")


if __name__ == "__main__":
    main()
