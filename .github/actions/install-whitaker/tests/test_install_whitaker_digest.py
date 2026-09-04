r"""Behavioural tests for the archive digest the verify step computes.

GNU coreutils `sha256sum` escapes its output line when the file name it was
given contains a backslash or a newline: it prefixes the line with a backslash
and escapes the offending characters in the name. Windows paths contain
backslashes, so a step that passes the name and reads the first field back gets
`\\<digest>` and rejects a correct archive.

These tests extract the digest helper from the manifest and run it under Bash
against exactly those names, so the fragment that ships is the one measured.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import typing as typ

import pytest
from _action_manifest import step_by_name
from hypothesis import given, settings
from hypothesis import strategies as st

from composite_fragments import require_posix_host

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

require_posix_host()

ARCHIVE_BYTES = b"whitaker installer archive contents"

#: File names that make `sha256sum` escape its output line. The backslash is
#: the Windows path separator, which is why this reached production; the
#: newline is the other name `sha256sum` escapes and is worth holding too.
ESCAPING_NAMES = {
    "backslash": "dir\\whitaker-installer.zip",
    "newline": "whitaker\ninstaller.zip",
    "plain": "whitaker-installer.zip",
}


def _digest_helper() -> str:
    """Return the `compute_sha256` definition from the verify step."""
    script = step_by_name("Verify Whitaker release")["run"]
    assert isinstance(script, str)
    match = re.search(
        r"^(?P<indent>[ \t]*)compute_sha256\(\) \{.*?^(?P=indent)\}$",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "verify step declares no compute_sha256 helper"
    return match.group(0)


def _run_helper(archive: Path, *, without_sha256sum: bool = False) -> str:
    """Return the digest the shipped helper computes for *archive*."""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    # Shadowing sha256sum with a failing lookup drives the shasum fallback,
    # which the macOS runners take.
    preamble = (
        "sha256sum() { return 127; }\ncommand() { return 1; }\n"
        if without_sha256sum
        else ""
    )
    script = f'set -euo pipefail\n{preamble}{_digest_helper()}\ncompute_sha256 "$1"\n'
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", script, "bash", str(archive)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture
def archive_factory(tmp_path: Path) -> typ.Callable[[str], Path]:
    """Return a factory writing the sample archive under a chosen name."""

    def factory(name: str) -> Path:
        archive = tmp_path / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(ARCHIVE_BYTES)
        return archive

    return factory


@pytest.mark.parametrize("name", sorted(ESCAPING_NAMES.values()))
def test_digest_is_unescaped_for_every_name(
    name: str, archive_factory: typ.Callable[[str], Path]
) -> None:
    """The computed digest must be the digest, whatever the file is called.

    This is the Windows failure: the archive was intact and the digest right,
    but a leading backslash on the parsed field rejected it.
    """
    digest = _run_helper(archive_factory(name))

    assert not digest.startswith("\\"), f"digest is escaped for {name!r}: {digest}"
    assert digest == hashlib.sha256(ARCHIVE_BYTES).hexdigest()


def test_digest_matches_across_the_fallback(
    archive_factory: typ.Callable[[str], Path],
) -> None:
    """The `shasum` fallback must agree with `sha256sum`, backslash and all."""
    archive = archive_factory(ESCAPING_NAMES["backslash"])

    fallback = _run_helper(archive, without_sha256sum=True)

    assert not fallback.startswith("\\")
    assert fallback == hashlib.sha256(ARCHIVE_BYTES).hexdigest()


def test_helper_never_passes_the_name_to_the_hasher() -> None:
    """Hash from standard input, so no name can reach the output to be escaped.

    Unescaping after the fact would work too, but only for the cases someone
    thought of; keeping the name out of the output removes the class.
    """
    helper = _digest_helper()

    assert 'sha256sum < "$1"' in helper
    assert 'shasum -a 256 < "$1"' in helper


def test_sidecar_digest_is_stripped_of_an_escape() -> None:
    """A sidecar written on an escaping host must still compare equal."""
    script = step_by_name("Verify Whitaker release")["run"]
    assert isinstance(script, str)

    assert 'sub(/^\\\\/, "", $1)' in script


def _verify_step_script() -> str:
    """Return the whole Bash fragment the verify step declares."""
    script = step_by_name("Verify Whitaker release")["run"]
    assert isinstance(script, str)
    return script


def _run_verify_step(
    staging_dir: Path, asset: str, *, sidecar: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the shipped verify step against a staged archive."""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    staging_dir.mkdir(parents=True, exist_ok=True)
    archive = staging_dir / asset
    archive.write_bytes(ARCHIVE_BYTES)
    digest = hashlib.sha256(ARCHIVE_BYTES).hexdigest()
    sidecar_line = f"{digest if sidecar is None else sidecar}  {asset}\n"
    archive.with_name(f"{asset}.sha256").write_text(sidecar_line, encoding="utf-8")
    environment = {
        **os.environ,
        "WHITAKER_ASSET": asset,
        "WHITAKER_EXPECTED_SHA": digest,
        "WHITAKER_NEEDS_INSTALL": "true",
        "WHITAKER_STAGING_DIR": str(staging_dir),
        "WHITAKER_TRUST_ANCHOR": "pinned",
        "WHITAKER_INSTALLER_VERSION": "0.2.7",
    }
    environment.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", _verify_step_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )


def test_verify_step_accepts_a_windows_shaped_staging_path(tmp_path: Path) -> None:
    """Drive the shipped step, not just its helper, over a backslash path.

    This is the production failure end to end: on Git Bash the staging path
    contains backslashes, and the step rejected an intact archive.
    """
    result = _run_verify_step(
        tmp_path / "runner\\_temp\\whitaker", "whitaker-installer.zip"
    )

    assert result.returncode == 0, result.stderr
    assert "digest mismatch" not in result.stderr
    assert "outcome=verified" in result.stdout


def test_verify_step_accepts_a_backslash_prefixed_sidecar(tmp_path: Path) -> None:
    """A sidecar written by an escaping host must still compare equal."""
    digest = hashlib.sha256(ARCHIVE_BYTES).hexdigest()

    result = _run_verify_step(
        tmp_path / "staging", "whitaker-installer.zip", sidecar=f"\\{digest}"
    )

    assert result.returncode == 0, result.stderr
    assert "disagrees with the verified archive digest" not in result.stderr


def test_verify_step_still_rejects_a_wrong_digest(tmp_path: Path) -> None:
    """Unescaping must not soften the check it protects.

    A sidecar that differs by more than an escape has to keep failing, or the
    fix would have traded a false rejection for a false acceptance.
    """
    result = _run_verify_step(
        tmp_path / "staging", "whitaker-installer.zip", sidecar="0" * 64
    )

    assert result.returncode != 0
    assert "disagrees with the verified archive digest" in result.stderr


#: Characters a path component may contain here. The backslash and newline are
#: the two `sha256sum` escapes; the rest are ordinary neighbours that must not
#: change the answer.
_COMPONENT_CHARACTERS = st.sampled_from("ab-_. \\\n")
PATH_COMPONENTS = st.text(_COMPONENT_CHARACTERS, min_size=1, max_size=12).filter(
    lambda component: component.strip(" .") not in {"", "..", "/"}
)


@given(component=PATH_COMPONENTS)
@settings(max_examples=40, derandomize=True, deadline=None)
def test_digest_is_name_independent(component: str, tmp_path_factory: object) -> None:
    """The digest depends on the bytes, never on what the file is called."""
    root = typ.cast("pytest.TempPathFactory", tmp_path_factory).mktemp("names")
    archive = root / component
    archive.write_bytes(ARCHIVE_BYTES)

    assert _run_helper(archive) == hashlib.sha256(ARCHIVE_BYTES).hexdigest()
    assert (
        _run_helper(archive, without_sha256sum=True)
        == hashlib.sha256(ARCHIVE_BYTES).hexdigest()
    )
