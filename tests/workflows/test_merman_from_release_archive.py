"""Contract that Merman CLI comes from a pinned archive, not a source build.

`ci.yml` used to install `merman-cli` with `cargo install`, behind an
`actions/cache` entry that saved and then never restored. The build was
therefore not occasional but constant, and at 5m 42s it was the largest
single step in the repository's slowest job. It also broke the estate's
rule that tools arrive as pinned, checksum-verified release archives.
Issue #483 carries the log extracts from both measured runs.

The rule has two halves and this module holds `ci.yml` to both. One test
says no step builds the tool from source. The other says a step really
does install it from the manifest, so that satisfying the first by
deleting the install altogether is not an option. A third ties the
version the workflow asks for to a version the manifest actually pins,
because an unpinned version reintroduces the unverified download this
change exists to remove.

Steps are matched by what they invoke, never by their names. A step
named "Install Merman CLI" that builds from source is exactly the defect
this rule prevents, and a differently named step that does the same is
no better.
"""

from __future__ import annotations

import re
import tomllib
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
TOOL_MANIFEST: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "tool-manifest.toml"

#: The tool as the manifest names it, which is also what the workflow asks for.
TOOL_NAME: typ.Final[str] = "merman-cli"

#: The action that installs a manifest tool from a digest-verified archive.
INSTALL_TOOL_ACTION: typ.Final[str] = "./.github/actions/install-tool"

#: A `cargo install` of this tool, as a command. `cargo` may carry a toolchain
#: selector such as `+1.95.0`, and `install` may be followed by any number of
#: options before the crate name, so both are allowed for between the words.
#: Anchored to a line start or a shell separator so that the phrase inside a
#: comment or a longer word is not read as an invocation.
#:
#: Without the options allowance, `cargo install --locked merman-cli` would
#: restore the source build while this contract still passed.
_CARGO_INSTALL: typ.Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|]\s*)cargo(?:\s+\+\S+)?\s+install\s+(?:-\S+(?:\s+\S+)?\s+)*"
    + re.escape(TOOL_NAME)
    + r"(?:\s|$)",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def ci_document() -> cabc.Mapping[str, typ.Any]:
    """Return the parsed `ci.yml`."""
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest_versions() -> cabc.Mapping[str, frozenset[str]]:
    """Return the versions the tool manifest pins, keyed by tool name."""
    document = tomllib.loads(TOOL_MANIFEST.read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for entry in document.get("tool", []):
        versions.setdefault(str(entry["name"]), set()).add(str(entry["version"]))
    return {name: frozenset(found) for name, found in versions.items()}


def _all_steps(document: cabc.Mapping[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return every step of every job, so no job can hide a source build."""
    return [
        step
        for job in (document.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
    ]


def _install_tool_steps(
    document: cabc.Mapping[str, typ.Any],
) -> list[dict[str, typ.Any]]:
    """Return the steps that install this tool through `install-tool`."""
    return [
        step
        for step in _all_steps(document)
        if step.get("uses") == INSTALL_TOOL_ACTION
        and str((step.get("with") or {}).get("tool", "")) == TOOL_NAME
    ]


def _resolve_workflow_expression(
    value: str, document: cabc.Mapping[str, typ.Any]
) -> str:
    """Resolve a bare `env.NAME` expression against the workflow's job envs.

    Only the one shape the workflow uses is resolved. Anything else is
    returned unchanged, so an unrecognised expression fails the version
    check loudly rather than being quietly accepted.
    """
    match = re.fullmatch(
        r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", value.strip()
    )
    if match is None:
        return value
    name = match.group(1)
    for job in (document.get("jobs") or {}).values():
        environment = job.get("env") or {}
        if name in environment:
            return str(environment[name])
    return value


def test_no_step_builds_merman_from_source(
    ci_document: cabc.Mapping[str, typ.Any],
) -> None:
    """No step in any job compiles the tool with `cargo install`.

    This is the defect the change removed. It reads as an ordinary
    install step and costs minutes on every pull request, because the
    cache meant to spare it never restored.
    """
    offenders = [
        step.get("name", "<unnamed>")
        for step in _all_steps(ci_document)
        if _CARGO_INSTALL.search(str(step.get("run", "")))
    ]
    assert not offenders, (
        f"ci.yml builds {TOOL_NAME} from source in {offenders}; install it "
        f"from the pinned archive with {INSTALL_TOOL_ACTION} instead"
    )


def test_a_step_installs_merman_from_the_manifest(
    ci_document: cabc.Mapping[str, typ.Any],
) -> None:
    """The workflow still installs the tool, through `install-tool`.

    This is the other half of the rule. Without it the source-build ban
    above is satisfied by removing the install entirely, which would
    leave the Mermaid validation step with no binary to run.
    """
    steps = _install_tool_steps(ci_document)
    assert steps, (
        f"ci.yml installs {TOOL_NAME} in no step; `make nixie` has no binary to run"
    )


def test_the_requested_version_is_one_the_manifest_pins(
    ci_document: cabc.Mapping[str, typ.Any],
    manifest_versions: cabc.Mapping[str, frozenset[str]],
) -> None:
    """The version the workflow asks for is pinned in the manifest.

    `install-tool` refuses a version it cannot resolve, so a drifted
    version fails the job rather than downloading something unverified.
    This asserts it at rest, where the cause is legible, instead of
    waiting for a red run to say it.
    """
    pinned = manifest_versions.get(TOOL_NAME, frozenset())
    requested = {
        _resolve_workflow_expression(
            str((step.get("with") or {}).get("version", "")), ci_document
        )
        for step in _install_tool_steps(ci_document)
    }
    unpinned = requested - pinned
    assert not unpinned, (
        f"ci.yml asks for {TOOL_NAME} {sorted(unpinned)}, which the tool "
        f"manifest does not pin; it pins {sorted(pinned)}"
    )


def test_a_cargo_install_of_another_crate_is_not_an_offence() -> None:
    """The source-build pattern matches this tool and not cargo in general.

    A rule that fired on every `cargo install` would pass its own
    mutation while discriminating nothing, so the narrow direction is
    asserted here rather than assumed.
    """
    assert not _CARGO_INSTALL.search("cargo install some-other-crate --locked")
    assert not _CARGO_INSTALL.search(f"# cargo install {TOOL_NAME} was removed")
    assert _CARGO_INSTALL.search(f"cargo +1.95.0 install {TOOL_NAME} --locked")
    assert _CARGO_INSTALL.search(f"cargo install --locked {TOOL_NAME}")
