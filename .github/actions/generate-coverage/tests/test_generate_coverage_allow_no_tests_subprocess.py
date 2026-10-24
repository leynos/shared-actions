"""Subprocess-boundary tests for ``allow-no-tests``, including cucumber.rs.

``test_generate_coverage_allow_no_tests`` calls ``main`` in the test's own
process with cargo replaced by a function. These tests run ``run_rust.py`` as
the action does, as a child process reading ``INPUT_*`` variables, with cargo
replaced by a temporary Python-backed command that records received argv in a
JSONL file without cmd-mox IPC. The four nextest/allow-no-tests settings cases
cover the cucumber.rs run as well, which the primary command's tests never
reach: it renders its own command, so a setting dropped on the way into it
would leave a cucumber-using workspace failing with exit 4 while the primary
run passed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
from test_scripts import run_script

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
NO_TESTS_FLAG = "--no-tests=pass"


class Case(typ.NamedTuple):
    """The two settings under test, and whether cargo should then see the flag.

    The flag is a nextest option, so both commands carry it only when nextest
    and the input are both on.
    """

    use_nextest: bool
    allow_no_tests: bool
    expected: bool


CASES = [
    pytest.param(
        Case(use_nextest=True, allow_no_tests=True, expected=True),
        id="nextest-and-allowed",
    ),
    pytest.param(
        Case(use_nextest=True, allow_no_tests=False, expected=False),
        id="nextest-not-allowed",
    ),
    pytest.param(
        Case(use_nextest=False, allow_no_tests=True, expected=False),
        id="plain-cargo-allowed",
    ),
    pytest.param(
        Case(use_nextest=False, allow_no_tests=False, expected=False),
        id="plain-cargo-not-allowed",
    ),
]


def _write_cargo_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Prepare a file-recording cargo command without an IPC server."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cargo = bin_dir / ("cargo.exe" if os.name == "nt" else "cargo")
    shutil.copy2(sys.executable, fake_cargo)
    venv_config = Path(sys.prefix) / "pyvenv.cfg"
    if venv_config.is_file():
        shutil.copy2(venv_config, tmp_path / "pyvenv.cfg")
    argv_log = tmp_path / "cargo-argv.jsonl"
    # Plumbum invokes `cargo llvm-cov ...`; the Python alias runs this script.
    (tmp_path / "llvm-cov").write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['CARGO_ARGV_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps(sys.argv) + '\\n')\n"
        "print('Coverage: 100%')\n",
        encoding="utf-8",
    )
    return bin_dir, argv_log


@pytest.mark.parametrize("case", CASES)
def test_both_cargo_commands_receive_the_flag_or_neither_does(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: Case,
) -> None:
    """The primary and the cucumber.rs commands agree on ``--no-tests=pass``."""
    out = tmp_path / "cov.lcov"
    cucumber = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    out.write_text("TN:test\nend_of_record\n")
    cucumber.write_text("TN:cuke\nend_of_record\n")
    bin_dir, argv_log = _write_cargo_stub(tmp_path)
    environment = {
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "CARGO_ARGV_LOG": str(argv_log),
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "DETECTED_CARGO_MANIFEST": "Cargo.toml",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_USE_CARGO_NEXTEST": "true" if case.use_nextest else "false",
        "INPUT_ALLOW_NO_TESTS": "true" if case.allow_no_tests else "false",
        "INPUT_WITH_CUCUMBER_RS": "true",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "GITHUB_OUTPUT": str(tmp_path / "gh.txt"),
    }
    monkeypatch.chdir(tmp_path)

    returncode, _, stderr = run_script(SCRIPT, environment)

    assert returncode == 0, stderr
    calls = [
        typ.cast("list[str]", json.loads(line))
        for line in argv_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(calls) == 2, calls
    primary, cucumber_run = calls
    assert (NO_TESTS_FLAG in primary) is case.expected, primary
    assert (NO_TESTS_FLAG in cucumber_run) is case.expected, cucumber_run
