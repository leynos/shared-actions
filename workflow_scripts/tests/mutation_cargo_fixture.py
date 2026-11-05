"""Install native recording Cargo launchers for mutation child-boundary tests."""

from __future__ import annotations

import os
import sys
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib


def install_recording_cargo(bin_dir: pathlib.Path, observed: tuple[str, ...]) -> None:
    """Create a fake Cargo without passing environment values through a shell.

    Parameters
    ----------
    bin_dir : pathlib.Path
        Existing directory to prepend to the test child's PATH.
    observed : tuple[str, ...]
        Safe environment names for the Python recorder to capture.

    Examples
    --------
    ``install_recording_cargo(bin_dir, ("RUSTFLAGS",))`` creates a launcher
    recording that variable and Cargo arguments in the child's RECORD file.
    """
    source = _recorder_source(observed)
    if os.name == "nt":
        recorder = bin_dir / "cargo_recorder.py"
        recorder.write_text(source, encoding="utf-8")
        # Only fixed fixture paths and Cargo argv enter batch text; flags remain env.
        (bin_dir / "cargo.cmd").write_text(
            f'@"{sys.executable}" "{recorder}" %*\n', encoding="utf-8"
        )
    else:
        cargo = bin_dir / "cargo"
        cargo.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
        cargo.chmod(0o700)


def _recorder_source(observed: tuple[str, ...]) -> str:
    """Return the same Python observation and exit contract on both platforms."""
    return (
        "import json, os, pathlib, sys\n"
        f"keys = {observed!r}\n"
        "record = {'argv': sys.argv[1:], 'env': "
        "{key: os.environ[key] for key in keys if key in os.environ}}\n"
        "pathlib.Path(os.environ['RECORD']).write_text(json.dumps(record))\n"
        "args = sys.argv[1:]\n"
        "base = args[args.index('--output') + 1] if '--output' in args else \
"
        "    args[args.index('--dir') + 1] if '--dir' in args else '.'\n"
        "directory = pathlib.Path(base) / 'mutants.out'\n"
        "directory.mkdir(parents=True, exist_ok=True)\n"
        "(directory / 'mutants.json').write_text('[{}]')\n"
        "sys.exit(int(os.environ.get('FAKE_CARGO_EXIT', '0')))\n"
    )
