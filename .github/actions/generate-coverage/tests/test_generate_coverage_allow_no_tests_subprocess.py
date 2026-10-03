"""Subprocess-boundary tests for ``allow-no-tests``, including cucumber.rs.

``test_generate_coverage_allow_no_tests`` calls ``main`` in the test's own
process with cargo replaced by a function. These tests run ``run_rust.py`` as
the action does, as a child process reading ``INPUT_*`` variables, with cargo
replaced by a cmd-mox stub, and read the argv cargo actually received. They
cover the cucumber.rs run as well, which the primary command's tests never
reach: it renders its own command, so a setting dropped on the way into it
would leave a cucumber-using workspace failing with exit 4 while the primary
run passed.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from test_scripts import run_script

from test_support.cmd_mox_stub_adapter import DefaultResponse

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    from test_support.cmd_mox_stub_adapter import StubManager

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
NO_TESTS_FLAG = "--no-tests=pass"

#: The two settings under test, with whether cargo should then see the flag
#: on both of its commands. The flag is a nextest option, so it needs both.
CASES = [
    pytest.param(True, True, True, id="nextest-and-allowed"),
    pytest.param(True, False, False, id="nextest-not-allowed"),
    pytest.param(False, True, False, id="plain-cargo-allowed"),
    pytest.param(False, False, False, id="plain-cargo-not-allowed"),
]


@pytest.mark.parametrize(("use_nextest", "allow_no_tests", "expected"), CASES)
def test_both_cargo_commands_receive_the_flag_or_neither_does(
    tmp_path: Path,
    shell_stubs: StubManager,
    monkeypatch: pytest.MonkeyPatch,
    use_nextest: bool,  # noqa: FBT001 - parametrised setting, not an API.
    allow_no_tests: bool,  # noqa: FBT001 - parametrised setting, not an API.
    expected: bool,  # noqa: FBT001 - parametrised expectation, not an API.
) -> None:
    """The primary and the cucumber.rs commands agree on ``--no-tests=pass``."""
    out = tmp_path / "cov.lcov"
    cucumber = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    out.write_text("TN:test\nend_of_record\n")
    cucumber.write_text("TN:cuke\nend_of_record\n")
    shell_stubs.register("cargo", default=DefaultResponse(stdout="Coverage: 100%\n"))
    environment = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "DETECTED_CARGO_MANIFEST": "Cargo.toml",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_USE_CARGO_NEXTEST": "true" if use_nextest else "false",
        "INPUT_ALLOW_NO_TESTS": "true" if allow_no_tests else "false",
        "INPUT_WITH_CUCUMBER_RS": "true",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "GITHUB_OUTPUT": str(tmp_path / "gh.txt"),
    }
    monkeypatch.chdir(tmp_path)

    returncode, _, stderr = run_script(SCRIPT, environment)

    assert returncode == 0, stderr
    primary, cucumber_run = (call.argv for call in shell_stubs.calls_of("cargo"))
    assert (NO_TESTS_FLAG in primary) is expected, primary
    assert (NO_TESTS_FLAG in cucumber_run) is expected, cucumber_run
