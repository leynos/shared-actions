"""Behavioural tests for the RUSTFLAGS export step's environment-file write.

These run the composite action's shell fragment as a subprocess, so they cover
the heredoc delimiter mechanism rather than the manifest's declared shape,
which ``test_manifest_input_step.py`` covers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rust_build_release_test_helpers import export_rustflags_run_script

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

# A value crafted to close a fixed heredoc delimiter early and have the
# remainder read back as further environment-file commands.
INJECTION_MARKER = "__RBR_RUSTFLAGS_EOF__"
INJECTED_RUSTFLAGS = f"-Zpolonius=next\n{INJECTION_MARKER}\nRBR_INJECTED=1"
# Scripted `od` output, shaped like the real 16-byte hex dump, used to make a
# delimiter collision reachable.
COLLIDING_OD_HEX = "0" * 32
SAFE_OD_HEX = "1" * 32

# Fragments chosen to provoke the environment-file parser: the old fixed
# marker, assignment and heredoc syntax, quoting, and newlines that could
# split a value across lines. Carriage returns and the other exotic
# separators Python's str.splitlines honours are excluded, because the
# runner splits environment files on newlines alone.
_PAYLOAD_FRAGMENTS = st.sampled_from(
    [
        "-D warnings",
        "-Zpolonius=next",
        INJECTION_MARKER,
        "RBR_INJECTED=1",
        "RUSTFLAGS<<EOF",
        "\n",
        " ",
        "\t",
        "=",
        "'",
        '"',
        "\\",
        "$RBR_RUSTFLAGS",
        "${{ inputs.rustflags }}",
        "détente-✓",
    ]
)
RUSTFLAGS_PAYLOADS = st.lists(_PAYLOAD_FRAGMENTS, min_size=1, max_size=10).map("".join)
# Each example spawns bash, so the default per-example deadline would flake
# on a loaded machine; the example count is bounded to keep the suite quick.
EXPORT_PROPERTY_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _delimiter_for(od_hex: str) -> str:
    """Return the delimiter the step derives from a given ``od`` output."""
    return f"__RBR_RUSTFLAGS_EOF_{od_hex}__"


def _write_od_stub(stubs_dir: Path) -> Path:
    """Install an ``od`` stub yielding one scripted value per invocation.

    The step derives its delimiter from ``od``, so replacing ``od`` on PATH is
    the only way to make a collision reachable; real output carries 128 bits of
    entropy. Values come from ``FAKE_OD_VALUES`` (one per line) and the last is
    repeated once exhausted, so a single value collides on every attempt.
    """
    stubs_dir.mkdir(parents=True, exist_ok=True)
    stub = stubs_dir / "od"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "count=0\n"
        'if [ -f "$FAKE_OD_CALLS" ]; then count="$(cat "$FAKE_OD_CALLS")"; fi\n'
        'printf \'%s\' "$((count + 1))" > "$FAKE_OD_CALLS"\n'
        'value="$(printf \'%s\\n\' "$FAKE_OD_VALUES" | sed -n "$((count + 1))p")"\n'
        'if [ -z "$value" ]; then\n'
        '  value="$(printf \'%s\\n\' "$FAKE_OD_VALUES" | tail -n 1)"\n'
        "fi\n"
        "printf '%s\\n' \"$value\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run_export_script(
    tmp_path: Path,
    rustflags: str,
    *,
    inherited: str | None = None,
    od_hex_values: tuple[str, ...] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the export fragment and return its result with the env-file text."""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - bash is present on supported runners
        pytest.skip("bash not found on PATH")
    tmp_path.mkdir(parents=True, exist_ok=True)
    github_env = tmp_path / "github-env"
    # Truncate rather than touch: a property test reuses one tmp_path across
    # examples, and the step appends, so a stale file would leak between them.
    github_env.write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "RUSTFLAGS"}
    env["GITHUB_ENV"] = github_env.as_posix()
    env["RBR_RUSTFLAGS"] = rustflags
    if inherited is not None:
        env["RUSTFLAGS"] = inherited
    if od_hex_values is not None:
        stubs_dir = tmp_path / "stubs"
        _write_od_stub(stubs_dir)
        env["PATH"] = f"{stubs_dir}{os.pathsep}{env['PATH']}"
        env["FAKE_OD_VALUES"] = "\n".join(od_hex_values)
        env["FAKE_OD_CALLS"] = (tmp_path / "od-calls").as_posix()
    result = subprocess.run(  # noqa: S603,TID251 - exercise the bash fragment.
        [bash, "-c", export_rustflags_run_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, github_env.read_text(encoding="utf-8")


def _parse_heredoc_value(
    lines: list[str], index: int, name: str, delimiter: str
) -> tuple[str, int]:
    """Collect a heredoc body, returning it with the index past its delimiter."""
    collected: list[str] = []
    while index < len(lines) and lines[index] != delimiter:
        collected.append(lines[index])
        index += 1
    if index >= len(lines):
        message = f"unterminated heredoc for {name}"
        raise AssertionError(message)
    return "\n".join(collected), index + 1


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse ``GITHUB_ENV`` content, honouring heredoc-delimited values."""
    values: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        name, separator, remainder = line.partition("=")
        if separator:
            values[name] = remainder
            continue
        name, separator, delimiter = line.partition("<<")
        if not separator:
            message = f"unparsable environment-file line: {line!r}"
            raise AssertionError(message)
        values[name], index = _parse_heredoc_value(lines, index, name, delimiter)
    return values


def test_export_rustflags_step_uses_a_generated_delimiter() -> None:
    """The heredoc delimiter must not be a fixed literal in the manifest."""
    run_script = export_rustflags_run_script()
    assert f"RUSTFLAGS<<{INJECTION_MARKER}" not in run_script, (
        "the manifest must not pin a fixed delimiter a caller could reproduce"
    )
    assert 'echo "RUSTFLAGS<<$delimiter"' in run_script, (
        "the heredoc must open with the generated delimiter variable"
    )


def test_export_rustflags_writes_single_line_value(tmp_path: Path) -> None:
    """An ordinary value round-trips through the environment file."""
    result, env_text = _run_export_script(tmp_path, "-Zpolonius=next")

    assert result.returncode == 0, result.stderr
    assert _parse_env_file(env_text) == {"RUSTFLAGS": "-Zpolonius=next"}, (
        f"the value must round-trip unchanged; env file held {env_text!r}"
    )
    assert "RUSTFLAGS exported from the rustflags input" in result.stderr, (
        f"the successful export should be announced; got {result.stderr!r}"
    )
    assert "-Zpolonius=next" not in result.stderr, (
        f"the exported value must stay out of the log; got {result.stderr!r}"
    )


def test_export_rustflags_contains_delimiter_lookalike(tmp_path: Path) -> None:
    """A value carrying the old fixed marker must not escape its heredoc."""
    result, env_text = _run_export_script(tmp_path, INJECTED_RUSTFLAGS)

    assert result.returncode == 0, result.stderr
    parsed = _parse_env_file(env_text)
    # The marker stays inside RUSTFLAGS rather than closing it, so nothing
    # after it is read back as a separate environment-file assignment.
    assert parsed == {"RUSTFLAGS": INJECTED_RUSTFLAGS}, (
        f"the marker must stay inside the value; env file held {env_text!r}"
    )
    assert "RBR_INJECTED" not in parsed, (
        "text after the marker must not become its own environment variable"
    )


def test_export_rustflags_delimiter_differs_between_runs(tmp_path: Path) -> None:
    """Delimiters are generated per run so callers cannot predict them."""
    _, first = _run_export_script(tmp_path / "first", "-Zpolonius=next")
    _, second = _run_export_script(tmp_path / "second", "-Zpolonius=next")

    first_header, second_header = first.splitlines()[0], second.splitlines()[0]
    assert first_header != second_header, (
        f"two runs reused the delimiter {first_header!r}"
    )


@pytest.mark.parametrize(
    ("case", "inherited"),
    [("non-empty", "-D warnings"), ("empty", "")],
    ids=["non-empty", "empty"],
)
def test_export_rustflags_defers_to_inherited_value(
    tmp_path: Path, case: str, inherited: str
) -> None:
    """An inherited RUSTFLAGS wins and nothing is written to the env file.

    The empty case is not a degenerate one: an empty value is still set, so it
    must take precedence over the input just as a non-empty value does.
    """
    result, env_text = _run_export_script(
        tmp_path / case, "-Zpolonius=next", inherited=inherited
    )

    assert result.returncode == 0, result.stderr
    assert env_text == "", (
        f"an inherited RUSTFLAGS of {inherited!r} must not be overwritten; "
        f"wrote {env_text!r}"
    )
    assert "leaving the inherited value in place" in result.stderr, (
        f"expected the deferral notice on stderr; got {result.stderr!r}"
    )


@EXPORT_PROPERTY_SETTINGS
@given(payload=RUSTFLAGS_PAYLOADS)
def test_exported_rustflags_round_trip_for_any_payload(
    tmp_path: Path, payload: str
) -> None:
    """Any payload survives the environment file as exactly one variable."""
    result, env_text = _run_export_script(tmp_path / "roundtrip", payload)

    assert result.returncode == 0, result.stderr
    # Exact equality is the delimiter-safety invariant: a value that closed
    # its heredoc early would either lose text or contribute extra names.
    assert _parse_env_file(env_text) == {"RUSTFLAGS": payload}, (
        f"payload {payload!r} did not round-trip; env file held {env_text!r}"
    )


@EXPORT_PROPERTY_SETTINGS
@given(payload=RUSTFLAGS_PAYLOADS, inherited=RUSTFLAGS_PAYLOADS | st.just(""))
def test_inherited_rustflags_always_wins(
    tmp_path: Path, payload: str, inherited: str
) -> None:
    """No payload can displace an inherited RUSTFLAGS, empty or otherwise."""
    result, env_text = _run_export_script(
        tmp_path / "precedence", payload, inherited=inherited
    )

    assert result.returncode == 0, result.stderr
    assert env_text == "", (
        f"payload {payload!r} overwrote inherited {inherited!r}; wrote {env_text!r}"
    )


def test_export_rustflags_retries_after_a_delimiter_collision(tmp_path: Path) -> None:
    """A candidate present in the value is discarded and another drawn."""
    payload = f"-Zpolonius=next\n{_delimiter_for(COLLIDING_OD_HEX)}"
    result, env_text = _run_export_script(
        tmp_path,
        payload,
        od_hex_values=(COLLIDING_OD_HEX, SAFE_OD_HEX),
    )

    assert result.returncode == 0, result.stderr
    assert env_text.startswith(f"RUSTFLAGS<<{_delimiter_for(SAFE_OD_HEX)}"), (
        f"the second candidate should have been used; env file held {env_text!r}"
    )
    assert _parse_env_file(env_text) == {"RUSTFLAGS": payload}, (
        f"the payload must still round-trip after a retry; got {env_text!r}"
    )
    assert (tmp_path / "od-calls").read_text(encoding="utf-8") == "2", (
        "exactly two candidates should have been drawn"
    )
    assert "attempt 1 of 3 collided" in result.stderr, (
        f"the discarded candidate should be reported; got {result.stderr!r}"
    )
    assert _delimiter_for(COLLIDING_OD_HEX) not in result.stderr, (
        "a colliding candidate is a line of the caller's value, so it must "
        f"never be logged; got {result.stderr!r}"
    )


def test_export_rustflags_fails_after_three_colliding_candidates(
    tmp_path: Path,
) -> None:
    """Three unusable candidates abort the step rather than corrupt the file."""
    payload = f"-Zpolonius=next\n{_delimiter_for(COLLIDING_OD_HEX)}"
    result, env_text = _run_export_script(
        tmp_path, payload, od_hex_values=(COLLIDING_OD_HEX,)
    )

    assert result.returncode == 1, (
        f"the step must fail rather than write an unsafe delimiter; {result.stderr!r}"
    )
    assert "could not derive a RUSTFLAGS delimiter" in result.stderr, (
        f"expected the give-up diagnostic on stderr; got {result.stderr!r}"
    )
    assert env_text == "", (
        f"nothing may reach the environment file on failure; wrote {env_text!r}"
    )
    assert (tmp_path / "od-calls").read_text(encoding="utf-8") == "3", (
        "the loop should try exactly three candidates before giving up"
    )
    assert result.stderr.count("collided with the value") == 3, (
        f"every attempt should be reported before giving up; got {result.stderr!r}"
    )
    assert _delimiter_for(COLLIDING_OD_HEX) not in result.stderr, (
        f"the colliding candidate must never be logged; got {result.stderr!r}"
    )
