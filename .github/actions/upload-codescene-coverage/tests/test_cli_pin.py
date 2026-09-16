"""Behavioural contracts for the pinned CodeScene coverage CLI build.

CodeScene serves the coverage CLI from a bucket that publishes only
``latest`` and one artefact per build commit SHA, and it rewrites the
installer script in place. An unpinned install therefore changes the
gate's verdict with no commit anywhere in the estate, and an empty
checksum makes the change undetectable. These contracts assert the
commands the action actually runs, not the identifiers it mentions.
"""

from __future__ import annotations

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


def _run_installer_step(
    tmp_path: Path,
    *,
    payload: str,
    inputs: cabc.Mapping[str, str] | None = None,
    pins: cabc.Mapping[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Execute the download step and return its result and step outputs.

    ``inputs`` supplies the caller's ``cli-version`` and
    ``installer-checksum``; an absent key is an unsupplied input, which
    GitHub renders as an empty environment value. ``pins`` overrides the
    action's own pinned constants.
    """
    supplied = dict(inputs or {})
    bash = _require_bash()
    step = _step(step_id="installer")
    declared = dict(step["env"])
    declared.update(pins or {})

    outputs = tmp_path / "github-output"
    outputs.write_text("", encoding="utf-8")
    curl_log = tmp_path / "curl.log"
    curl_log.write_text("", encoding="utf-8")
    stub_dir = _stub_curl(tmp_path, payload)

    env = os.environ | {
        "PINNED_CLI_VERSION": declared["PINNED_CLI_VERSION"],
        "PINNED_INSTALLER_SHA256": declared["PINNED_INSTALLER_SHA256"],
        "CLI_VERSION": supplied.get("cli-version", ""),
        "CODESCENE_CLI_SHA256": supplied.get("installer-checksum", ""),
        "GITHUB_OUTPUT": str(outputs),
        "CURL_LOG": str(curl_log),
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
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


def test_pinned_build_is_a_commit_sha() -> None:
    """The action pins the CLI to one immutable build artefact."""
    pinned = _installer_env()["PINNED_CLI_VERSION"]

    assert pinned != "latest"
    assert COMMIT_SHA.fullmatch(pinned), pinned


def test_pinned_installer_digest_is_a_sha256() -> None:
    """The action carries a non-empty digest for the installer script."""
    digest = _installer_env()["PINNED_INSTALLER_SHA256"]

    assert digest
    assert SHA256.fullmatch(digest), digest


def test_cli_version_input_does_not_default_to_a_floating_build() -> None:
    """An unsupplied cli-version resolves to the pin, not to 'latest'."""
    manifest = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))

    assert manifest["inputs"]["cli-version"].get("default", "") == ""


def test_unsupplied_version_resolves_to_the_pinned_build(
    tmp_path: Path,
) -> None:
    """With no cli-version the step installs the pinned build and caches it."""
    result, outputs = _run_installer_step(
        tmp_path,
        payload=INSTALLER_PAYLOAD,
        pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
    )

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == _installer_env()["PINNED_CLI_VERSION"]
    assert outputs["cacheable"] == "true"


def test_explicit_build_overrides_the_pin(
    tmp_path: Path,
) -> None:
    """A caller-supplied build is installed and remains cacheable."""
    ahead = "0" * 40
    result, outputs = _run_installer_step(
        tmp_path,
        payload=INSTALLER_PAYLOAD,
        inputs={"cli-version": ahead},
        pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
    )

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == ahead
    assert outputs["cacheable"] == "true"


def test_latest_remains_available_and_uncacheable(
    tmp_path: Path,
) -> None:
    """The floating build stays reachable but must never be cached."""
    result, outputs = _run_installer_step(
        tmp_path,
        payload=INSTALLER_PAYLOAD,
        inputs={"cli-version": "latest"},
        pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
    )

    assert result.returncode == 0, result.stderr
    assert outputs["version"] == "latest"
    assert outputs["cacheable"] == "false"


def test_installer_that_changed_under_the_pin_fails_the_step(
    tmp_path: Path,
) -> None:
    """A rewritten installer script stops the step and names the digest."""
    expected = _digest(INSTALLER_PAYLOAD)
    result, outputs = _run_installer_step(
        tmp_path,
        payload=REWRITTEN_PAYLOAD,
        pins={"PINNED_INSTALLER_SHA256": expected},
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
        payload=REWRITTEN_PAYLOAD,
        inputs={"installer-checksum": ""},
        pins={"PINNED_INSTALLER_SHA256": pin},
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
        payload=payload,
        inputs={"installer-checksum": _digest(payload)},
        pins={"PINNED_INSTALLER_SHA256": _digest("something else\n")},
    )

    assert result.returncode == 0, result.stderr
    assert outputs["cacheable"] == "true"


def test_no_digest_anywhere_refuses_to_install(
    tmp_path: Path,
) -> None:
    """With the pin emptied and no override the step refuses to proceed."""
    result, _ = _run_installer_step(
        tmp_path,
        payload=INSTALLER_PAYLOAD,
        pins={"PINNED_INSTALLER_SHA256": ""},
    )

    assert result.returncode != 0
    assert "No SHA-256 to verify" in result.stderr


def test_step_downloads_the_documented_installer(
    tmp_path: Path,
) -> None:
    """The verified artefact is the CodeScene installer, not another URL."""
    _run_installer_step(
        tmp_path,
        payload=INSTALLER_PAYLOAD,
        pins={"PINNED_INSTALLER_SHA256": _digest(INSTALLER_PAYLOAD)},
    )

    assert INSTALLER_URL in (tmp_path / "curl.log").read_text(encoding="utf-8")


def test_cli_cache_key_cannot_restore_a_different_build() -> None:
    """The CLI cache is keyed exactly, with no prefix fallback."""
    cache = _step(step_id="cs-cache")

    assert "restore-keys" not in cache["with"]
    assert "steps.installer.outputs.version" in cache["with"]["key"]


def test_cli_cache_follows_the_resolved_build_not_the_raw_input() -> None:
    """Caching is decided by the resolved build, so the pin is cached."""
    assert (
        "steps.installer.outputs.cacheable == 'true'" in _step(step_id="cs-cache")["if"]
    )


def test_install_command_receives_the_resolved_build(tmp_path: Path) -> None:
    """The installer is invoked with the resolved build as its argument."""
    bash = _require_bash()
    step = _step(name="Install CodeScene Coverage CLI")

    assert step["env"]["CLI_VERSION"] == "${{ steps.installer.outputs.version }}"

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
