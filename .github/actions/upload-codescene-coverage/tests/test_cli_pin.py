"""Behavioural contracts for the pinned CodeScene coverage CLI build.

CodeScene serves the coverage CLI from a bucket that publishes only
``latest`` and one artefact per build commit SHA, and it rewrites the
installer script in place. An unpinned install therefore changes the
gate's verdict with no commit anywhere in the estate, and an empty
checksum makes the change undetectable. These contracts assert the
commands the action actually runs, not the identifiers it mentions.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import os
import re
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc

ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"

COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")

#: The build this action pins, and the installer script's digest at it,
#: written out rather than read from the manifest. A shape check passes
#: for any forty hexadecimal characters, so it cannot tell the recorded
#: pin from a different build someone moved it to; these two constants
#: can. Moving the pin is then a change to this file as well, which is
#: the decision the pin exists to force.
PINNED_BUILD = "ce87259e704bde6038d1a6904ce4dbf56359e5ee"
PINNED_INSTALLER_DIGEST = (
    "ec9279ce87b523b6e958a8d551283484c391d3de9f5dd2ba2b4f784ecf19b1e4"
)

INSTALLER_URL = (
    "https://downloads.codescene.io/enterprise/cli/install-cs-coverage-tool.sh"
)


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action steps."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"]


def _step(*, name: str | None = None, step_id: str | None = None) -> dict[str, typ.Any]:
    """Return the single step matching ``name`` or ``step_id``."""
    return next(
        step
        for step in _steps()
        if (name is None or step.get("name") == name)
        and (step_id is None or step.get("id") == step_id)
    )


def _installer_env() -> cabc.Mapping[str, str]:
    """Return the download step's declared environment."""
    return _step(step_id="installer")["env"]


def _resolve_env() -> cabc.Mapping[str, str]:
    """Return the build-resolution step's declared environment."""
    return _step(step_id="resolve-cli")["env"]


def _require_bash() -> str:
    """Return a bash interpreter, skipping where the platform has none."""
    if sys.platform == "win32":
        pytest.skip("bash integration tests are not supported on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return bash


def _stub_curl(tmp_path: Path, payload: str) -> Path:
    """Install a curl stub that writes ``payload`` to the requested path."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    served = tmp_path / "served-installer.sh"
    served.write_text(payload, encoding="utf-8")
    curl = stub_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        'printf "curl-invoked: %s\\n" "$*" >>"$CURL_LOG"\n'
        "dest=\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "-o" ]; then\n'
        "    dest=$2\n"
        "    shift\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        f'cp "{served}" "$dest"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    return stub_dir


#: The non-builtin commands that must resolve for the step to run, on
#: top of whichever checksum tool a case allows. `mktemp` is the step's;
#: `bash` and `cp` are the curl stub's, whose shebang goes through `env`
#: and which copies the served payload into place. `printf`, `command`
#: and `set` are bash builtins and need no entry.
_ESSENTIAL_TOOLS: typ.Final[tuple[str, ...]] = ("mktemp", "bash", "cp")


def _tool_dir(tmp_path: Path, checksum_tools: cabc.Sequence[str]) -> Path:
    """Return a bin directory holding only *checksum_tools* and the essentials.

    Building the directory rather than filtering the real PATH is what
    makes the macOS case reachable from Linux: a runner image without
    `sha256sum` is exactly a PATH on which only `shasum` resolves.
    """
    tools = tmp_path / "tool-bin"
    tools.mkdir(exist_ok=True)
    for name in (*checksum_tools, *_ESSENTIAL_TOOLS):
        located = shutil.which(name)
        if located is None:
            pytest.skip(f"{name} not found on PATH")
        link = tools / name
        if not link.exists():
            link.symlink_to(located)
    return tools


def _run_resolve_step(
    tmp_path: Path,
    *,
    inputs: cabc.Mapping[str, str] | None = None,
    pins: cabc.Mapping[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Execute the build-resolution step and return its result and outputs.

    It touches no network, which is the point of separating it: the
    cache key it feeds can be computed whether or not the installer can
    be downloaded.
    """
    supplied = dict(inputs or {})
    bash = _require_bash()
    step = _step(step_id="resolve-cli")
    declared = dict(step["env"])
    declared.update(pins or {})

    outputs = tmp_path / "github-output"
    outputs.write_text("", encoding="utf-8")
    env = os.environ | {
        "PINNED_CLI_VERSION": declared["PINNED_CLI_VERSION"],
        "CLI_VERSION": supplied.get("cli-version", ""),
        "GITHUB_OUTPUT": str(outputs),
    }
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )
    parsed = dict(
        line.split("=", 1)
        for line in outputs.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return result, parsed


@dc.dataclass(frozen=True, slots=True)
class InstallerCase:
    """One run of the download step: what it is served and what it may find.

    Attributes
    ----------
    payload : str
        The installer script the stubbed curl writes.
    inputs : cabc.Mapping[str, str]
        The caller's ``installer-checksum``. An absent key is an
        unsupplied input, which GitHub renders as an empty environment
        value rather than an absent one.
    pins : cabc.Mapping[str, str]
        Overrides for the action's own pinned constants, because no
        payload can be made to hash to the recorded pin.
    checksum_tools : cabc.Sequence[str]
        The checksum commands the step is allowed to find, so that a
        runner image providing only one of them can be reproduced here.
    """

    payload: str
    inputs: cabc.Mapping[str, str] = dc.field(default_factory=dict)
    pins: cabc.Mapping[str, str] = dc.field(default_factory=dict)
    checksum_tools: cabc.Sequence[str] = ("sha256sum", "shasum")


def _run_installer_step(
    tmp_path: Path, case: InstallerCase
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Execute the download step and return its result and step outputs."""
    payload = case.payload
    supplied = dict(case.inputs)
    bash = _require_bash()
    step = _step(step_id="installer")
    declared = dict(step["env"])
    declared.update(case.pins)

    outputs = tmp_path / "github-output"
    outputs.write_text("", encoding="utf-8")
    curl_log = tmp_path / "curl.log"
    curl_log.write_text("", encoding="utf-8")
    stub_dir = _stub_curl(tmp_path, payload)

    env = os.environ | {
        "PINNED_INSTALLER_SHA256": declared["PINNED_INSTALLER_SHA256"],
        "CODESCENE_CLI_SHA256": supplied.get("installer-checksum", ""),
        "GITHUB_OUTPUT": str(outputs),
        "CURL_LOG": str(curl_log),
        "PATH": f"{stub_dir}{os.pathsep}{_tool_dir(tmp_path, case.checksum_tools)}",
    }
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", str(step["run"])],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )
    parsed = dict(
        line.split("=", 1)
        for line in outputs.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return result, parsed


# A test cannot serve the real installer script without a network, and
# no payload can be made to hash to the recorded pin, so tests that
# exercise verification repin PINNED_INSTALLER_SHA256 to the payload
# they serve. The recorded pin's own shape is asserted separately.
INSTALLER_PAYLOAD = "#!/bin/sh\necho 'CodeScene installer'\n"
REWRITTEN_PAYLOAD = "#!/bin/sh\necho 'rewritten in place'\n"


def _digest(payload: str) -> str:
    """Return the SHA-256 of ``payload`` as the installer would see it."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_pinned_build_is_the_recorded_one() -> None:
    """The action pins the CLI to the build this contract records.

    Equality, not shape. A shape check accepts any forty hexadecimal
    characters, so the pin could move to a different build, or back to a
    floating one written out longhand, with this test still green. The
    format assertions stay as diagnostics for the case where the two
    disagree.
    """
    pinned = _resolve_env()["PINNED_CLI_VERSION"]

    assert pinned != "latest"
    assert COMMIT_SHA.fullmatch(pinned), pinned
    assert pinned == PINNED_BUILD, (
        f"the action pins build {pinned}, and this contract records "
        f"{PINNED_BUILD}; moving the pin is a decision and belongs in both"
    )


def test_pinned_installer_digest_is_the_recorded_one() -> None:
    """The installer digest is the one recorded for the pinned build.

    The digest and the build travel together: the installer script is
    rewritten in place, so a digest that no longer matches its build
    verifies nothing about the artefact that gets installed.
    """
    digest = _installer_env()["PINNED_INSTALLER_SHA256"]

    assert digest
    assert SHA256.fullmatch(digest), digest
    assert digest == PINNED_INSTALLER_DIGEST, (
        f"the action carries installer digest {digest}, and this contract "
        f"records {PINNED_INSTALLER_DIGEST}"
    )


def test_cli_version_input_does_not_default_to_a_floating_build() -> None:
    """An unsupplied cli-version resolves to the pin, not to 'latest'."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))

    assert manifest["inputs"]["cli-version"].get("default", "") == ""


def test_unsupplied_version_resolves_to_the_pinned_build(
    tmp_path: Path,
) -> None:
    """With no cli-version the step resolves the pinned build and caches it."""
    result, outputs = _run_resolve_step(tmp_path)

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == PINNED_BUILD
    assert outputs["cacheable"] == "true"


def test_explicit_build_overrides_the_pin(
    tmp_path: Path,
) -> None:
    """A caller-supplied build is resolved and remains cacheable."""
    ahead = "0" * 40
    result, outputs = _run_resolve_step(tmp_path, inputs={"cli-version": ahead})

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == ahead
    assert outputs["cacheable"] == "true"


def test_latest_remains_available_and_uncacheable(
    tmp_path: Path,
) -> None:
    """The floating build stays reachable but must never be cached."""
    result, outputs = _run_resolve_step(tmp_path, inputs={"cli-version": "latest"})

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == "latest"
    assert outputs["cacheable"] == "false"


def test_resolving_the_build_touches_no_network(tmp_path: Path) -> None:
    """The resolution step runs with no curl on PATH at all.

    This is the separation the cache depends on. If resolution needed the
    download, an exact cache hit would still be gated on fetching a
    script the run would then throw away.
    """
    result, outputs = _run_resolve_step(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "script" not in outputs, (
        "the resolution step must not produce an installer script; the "
        "download belongs behind the cache lookup"
    )


def test_installer_that_changed_under_the_pin_fails_the_step(
    tmp_path: Path,
) -> None:
    """A rewritten installer script stops the step and names the digest."""
    expected = _digest(INSTALLER_PAYLOAD)
    result, outputs = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=REWRITTEN_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": expected},
        ),
    )

    assert result.returncode != 0
    assert expected in result.stderr
    assert "script" not in outputs


def test_unsupplied_checksum_still_verifies_against_the_pin(
    tmp_path: Path,
) -> None:
    """An empty installer-checksum input falls back to the pinned digest.

    Consumers pass the input from a repository variable that is often
    unset, which arrives as the empty string. Treating that as "skip
    verification" is the defect this pin exists to remove.
    """
    pin = _digest(INSTALLER_PAYLOAD)
    result, _ = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=REWRITTEN_PAYLOAD,
            inputs={"installer-checksum": ""},
            pins={"PINNED_INSTALLER_SHA256": pin},
        ),
    )

    assert result.returncode != 0
    assert pin in result.stderr


def test_supplied_checksum_takes_precedence_over_the_pin(
    tmp_path: Path,
) -> None:
    """A caller ahead of the pin verifies against its own digest."""
    payload = "#!/bin/sh\necho 'newer installer'\n"
    result, outputs = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=payload,
            inputs={"installer-checksum": _digest(payload)},
            pins={"PINNED_INSTALLER_SHA256": _digest("something else\n")},
        ),
    )

    assert result.returncode == 0, result.stderr
    assert outputs["script"]


def test_no_digest_anywhere_refuses_to_install(
    tmp_path: Path,
) -> None:
    """With the pin emptied and no override the step refuses to proceed."""
    result, _ = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=INSTALLER_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": ""},
        ),
    )

    assert result.returncode != 0
    assert "No SHA-256 to verify" in result.stderr


def test_step_downloads_the_documented_installer(
    tmp_path: Path,
) -> None:
    """The verified artefact is the CodeScene installer, not another URL."""
    _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=INSTALLER_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
        ),
    )

    assert INSTALLER_URL in (tmp_path / "curl.log").read_text(encoding="utf-8")


def test_cli_cache_key_cannot_restore_a_different_build() -> None:
    """The CLI cache is keyed exactly, with no prefix fallback."""
    cache = _step(step_id="cs-cache")

    assert "restore-keys" not in cache["with"]
    assert "steps.resolve-cli.outputs.version" in cache["with"]["key"]


def test_cli_cache_key_distinguishes_the_runner_architecture() -> None:
    """The cache key names the CPU as well as the operating system.

    The installer fetches a different artefact for aarch64 and for
    amd64 and puts both at the same path. GitHub's cache version covers
    the path and the compression tool and not the architecture, so
    without this an arm64 Linux runner restores an x86-64 binary under
    the same key, the install step is skipped on the hit, and the
    failure surfaces much later as an exec-format error.
    """
    key = _step(step_id="cs-cache")["with"]["key"]

    assert "runner.arch" in key, (
        f"the CLI cache key is {key!r} and does not name the architecture; "
        "same-OS runners of different CPUs would share one entry"
    )
    assert "runner.os" in key, key


def test_cli_cache_follows_the_resolved_build_not_the_raw_input() -> None:
    """Caching is decided by the resolved build, so the pin is cached."""
    assert (
        "steps.resolve-cli.outputs.cacheable == 'true'"
        in _step(step_id="cs-cache")["if"]
    )


def test_the_download_runs_only_when_the_cache_missed() -> None:
    """The installer download sits behind the cache lookup, not in front.

    Asserting the order as well as the condition, because a download
    guarded on a cache hit that has not happened yet is guarded on an
    empty string and always runs.
    """
    steps = _steps()
    order = [step.get("id") for step in steps]
    assert order.index("resolve-cli") < order.index("cs-cache"), order
    assert order.index("cs-cache") < order.index("installer"), order

    condition = " ".join(str(_step(step_id="installer")["if"]).split())
    assert "steps.cs-cache.outputs.cache-hit != 'true'" in condition, condition


def test_install_command_receives_the_resolved_build(tmp_path: Path) -> None:
    """The installer is invoked with the resolved build as its argument."""
    bash = _require_bash()
    step = _step(name="Install CodeScene Coverage CLI")

    assert step["env"]["CLI_VERSION"] == "${{ steps.resolve-cli.outputs.version }}"

    installer = tmp_path / "installer.sh"
    installer.write_text(
        '#!/usr/bin/env bash\nprintf "installer arguments: %s\\n" "$*"\n',
        encoding="utf-8",
    )
    script = str(step["run"]).replace(
        "${{ steps.installer.outputs.script }}", str(installer)
    )
    result = subprocess.run(  # noqa: S603,TID251 - exercise the action's bash.
        [bash, "-c", script],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ | {"CLI_VERSION": "0" * 40},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"installer arguments: -y {'0' * 40}" in result.stdout


@pytest.mark.parametrize(
    "checksum_tools",
    [
        pytest.param(("sha256sum",), id="only-sha256sum"),
        pytest.param(("shasum",), id="only-shasum"),
        pytest.param(("sha256sum", "shasum"), id="both"),
    ],
)
def test_verification_works_with_either_checksum_tool(
    tmp_path: Path, checksum_tools: tuple[str, ...]
) -> None:
    """The step verifies on an image providing either command.

    macOS runner images ship `shasum` and not `sha256sum`, so the
    unconditional `sha256sum -c -` this step used to run failed there
    before it could install anything. The `only-shasum` case is that
    image, reproduced by giving the step a PATH on which nothing else
    resolves.
    """
    result, outputs = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=INSTALLER_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
            checksum_tools=checksum_tools,
        ),
    )

    assert result.returncode == 0, result.stderr
    assert outputs["script"], (
        f"with {list(checksum_tools)} on PATH the installer must verify and "
        f"report its script; stderr was {result.stderr!r}"
    )


@pytest.mark.parametrize(
    "checksum_tools",
    [
        pytest.param(("sha256sum",), id="only-sha256sum"),
        pytest.param(("shasum",), id="only-shasum"),
    ],
)
def test_a_rewritten_installer_fails_with_either_checksum_tool(
    tmp_path: Path, checksum_tools: tuple[str, ...]
) -> None:
    """Whichever tool verifies, a changed script still stops the step.

    The narrow direction. A selection that fell back to something which
    always succeeds would pass the test above and verify nothing.
    """
    expected = _digest(INSTALLER_PAYLOAD)
    result, outputs = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=REWRITTEN_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": expected},
            checksum_tools=checksum_tools,
        ),
    )

    assert result.returncode != 0, result.stdout
    assert expected in result.stderr
    assert "script" not in outputs


def test_no_checksum_tool_at_all_refuses_to_install(tmp_path: Path) -> None:
    """With neither command on PATH the step stops rather than skipping.

    An image providing neither is not a reason to install unverified
    bytes, and a `command -v` chain with no final `else` would do exactly
    that.
    """
    result, outputs = _run_installer_step(
        tmp_path,
        InstallerCase(
            payload=INSTALLER_PAYLOAD,
            pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
            checksum_tools=(),
        ),
    )

    assert result.returncode != 0
    assert "shasum" in result.stderr
    assert "script" not in outputs
