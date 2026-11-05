"""Check bounded Rustflags command properties without spawning child processes.

Generated values are Unicode scalar strings of at most 256 characters, excluding
CR, LF and NUL. Invalid cases insert each forbidden character into two such
strings. These sampled command-boundary properties complement the finite real
child tests; they do not prove arbitrary lengths or operating-system behaviour.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import typing as typ
from unittest import mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from workflow_scripts import mutation_cargo_command as command
from workflow_scripts import mutation_run_cargo as runner

if typ.TYPE_CHECKING:
    from plumbum.machines.local import LocalMachine

SINGLE_LINE = st.text(
    alphabet=st.characters(exclude_categories=("Cs",), exclude_characters="\r\n\0"),
    max_size=256,
)
NONEMPTY_LINE = st.text(
    alphabet=st.characters(exclude_categories=("Cs",), exclude_characters="\r\n\0"),
    min_size=1,
    max_size=256,
)
ARGUMENTS = ["mutants", "--in-place", "--timeout-multiplier", "3"]


@dataclasses.dataclass(frozen=True, slots=True)
class _Binding:
    """Record a resolved executable and its literal, unexecuted arguments."""

    executable: str
    arguments: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Executable:
    """Bind arguments without parsing them or invoking a real tool."""

    executable: str

    def __getitem__(self, arguments: list[str] | tuple[str, ...]) -> _Binding:
        """Capture the exact arguments handed to this executable."""
        return _Binding(self.executable, tuple(arguments))


@dataclasses.dataclass(slots=True)
class _Provider:
    """Record tool lookups while retaining an explicit inherited environment."""

    environment: dict[str, str]
    lookups: list[str] = dataclasses.field(default_factory=list)

    def __getitem__(self, name: str) -> _Executable:
        """Resolve fixed test executables without touching PATH or the environment."""
        self.lookups.append(name)
        return _Executable({"cargo": "/resolved/cargo", "env": "/resolved/env"}[name])


def _binding(provider: _Provider, rustflags: str) -> _Binding:
    """Call the production builder through its command-provider protocol."""
    # This fake implements only command binding; it deliberately cannot execute.
    parent_environment = dict(os.environ)
    bound = command.cargo_command(
        typ.cast("LocalMachine", provider), ARGUMENTS, rustflags
    )
    assert dict(os.environ) == parent_environment, (
        "Command construction must not mutate the parent process environment"
    )
    assert isinstance(bound, _Binding), (
        "Command construction must return the bound tool"
    )
    return bound


@given(replacement=NONEMPTY_LINE, inherited=SINGLE_LINE, encoded=SINGLE_LINE)
def test_generated_override_is_one_literal_assignment(
    replacement: str, inherited: str, encoded: str
) -> None:
    """Every generated replacement preserves the resolved Cargo and fixed argv."""
    environment = {
        "RUSTFLAGS": inherited,
        "CARGO_ENCODED_RUSTFLAGS": encoded,
        "UNRELATED": "preserved",
    }
    provider = _Provider(environment.copy())
    original_arguments = ARGUMENTS.copy()
    bound = _binding(provider, replacement)
    assert bound == _Binding(
        "/resolved/env",
        (
            "-u",
            "CARGO_ENCODED_RUSTFLAGS",
            "RUSTFLAGS=" + replacement,
            "/resolved/cargo",
            *original_arguments,
        ),
    ), "Override must remove the encoded key and pass one literal replacement to Cargo"
    assert provider.lookups == ["cargo", "env"], (
        "Both tools must use the injected provider"
    )
    assert provider.environment == environment, (
        "Command binding must not mutate inheritance"
    )
    assert original_arguments == ARGUMENTS, (
        "Command binding must not mutate caller argv"
    )


@given(inherited=SINGLE_LINE, encoded=SINGLE_LINE)
def test_generated_empty_override_preserves_direct_cargo_binding(
    inherited: str, encoded: str
) -> None:
    """An empty replacement adds no environment wrapper or environment edits."""
    environment = {"RUSTFLAGS": inherited, "CARGO_ENCODED_RUSTFLAGS": encoded}
    provider = _Provider(environment.copy())
    bound = _binding(provider, "")
    assert bound == _Binding("/resolved/cargo", tuple(ARGUMENTS)), (
        "Empty replacement must bind the same Cargo executable and arguments directly"
    )
    assert provider.lookups == ["cargo"], "Default mode must not resolve an env wrapper"
    assert provider.environment == environment, (
        "Both inherited flag values must be unchanged"
    )


@given(
    prefix=SINGLE_LINE,
    suffix=SINGLE_LINE,
    forbidden=st.sampled_from(["\r", "\n", "\0"]),
)
def test_generated_invalid_values_report_only_mode_before_tool_lookup(
    prefix: str, suffix: str, forbidden: str
) -> None:
    """Invalid flags emit a safe mode and fixed diagnostic before any lookup."""
    provider = _Provider(
        {"RUSTFLAGS": "inherited", "CARGO_ENCODED_RUSTFLAGS": "encoded"}
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    parent_environment = dict(os.environ)
    with (
        mock.patch.object(runner, "local", provider),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
        pytest.raises(SystemExit) as raised,
    ):
        runner.main(rustflags=prefix + forbidden + suffix)
    assert dict(os.environ) == parent_environment, (
        "Invalid flags must not mutate the parent process environment"
    )
    assert raised.value.code == 1, (
        "Invalid flags must preserve the input-error exit code"
    )
    assert provider.lookups == [], "Validation must finish before Cargo or env lookup"
    assert stdout.getvalue() == (
        "mutation_cargo_command=" + json.dumps(["cargo", *ARGUMENTS]) + "\n"
        "mutation_cargo_rustflags_mode=invalid\n"
    ), "Invalid input must add only its mode to existing command reporting"
    assert stderr.getvalue() == (
        "automerge_status=error\n"
        "automerge_error=rustflags must be a single line without CR, LF or NUL\n"
    ), "The fixed diagnostic must never include generated flag contents"
    assert provider.environment == {
        "RUSTFLAGS": "inherited",
        "CARGO_ENCODED_RUSTFLAGS": "encoded",
    }, "Rejected values must not modify the inherited environment"
