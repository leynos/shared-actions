r"""Tests for Windows path handling in the install-whitaker lifecycle.

`RUNNER_TEMP` is a native path, so under Git Bash the staging directory arrives
as `D:\a\_temp/whitaker-installer-release`. GNU tar reads a colon in an archive
path as rmt's `host:path` syntax and tries to resolve `D` as a hostname, which
fails after the archive has already been downloaded and verified.

Two defences, and these tests hold both: the path is normalized to POSIX form
where it is first produced, and GNU tar is additionally told to treat a colon
as part of the name. The extract fragment is executed under Bash against a stub
`tar` that records its arguments, so what is measured is the shipped step.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as typ

import pytest
from _action_manifest import step_by_name
from _fragment_runner import require_posix_host

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

require_posix_host()

#: A staging directory in the shape Git Bash hands the action on Windows.
WINDOWS_STAGING_DIR = r"D:\a\_temp/whitaker-installer-release"
ASSET = "whitaker-installer-x86_64-pc-windows-msvc-v0.2.7.zip"


def _extract_script() -> str:
    """Return the Bash fragment the extract step declares."""
    script = step_by_name("Extract Whitaker installer")["run"]
    assert isinstance(script, str)
    return script


def _stub_tar(directory: Path, *, gnu: bool) -> Path:
    """Write a stub `tar` that records its arguments, and return its log."""
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "tar-args.log"
    version = "tar (GNU tar) 1.35" if gnu else "bsdtar 3.7.2"
    reject = (
        ""
        if gnu
        else (
            'for arg in "$@"; do\n'
            '  if [ "$arg" = --force-local ]; then\n'
            '    echo "tar: unrecognized option --force-local" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "done\n"
        )
    )
    (directory / "tar").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = --version ]; then\n'
        f'  echo "{version}"\n'
        "  exit 0\n"
        "fi\n"
        f"{reject}"
        f'printf "%s\\n" "$@" > "{log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (directory / "tar").chmod(0o755)
    return log


def _run_extract(
    tmp_path: Path, *, staging_dir: str, gnu: bool
) -> subprocess.CompletedProcess[str]:
    """Run the extract fragment with `tar` stubbed."""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    stub_dir = tmp_path / "stubs"
    _stub_tar(stub_dir, gnu=gnu)
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "WHITAKER_ASSET": ASSET,
        "WHITAKER_NEEDS_INSTALL": "true",
        "WHITAKER_STAGING_DIR": staging_dir,
        "WHITAKER_INSTALLER_VERSION": "0.2.7",
    }
    environment.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", _extract_script()],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
        timeout=30,
    )


def _tar_arguments(tmp_path: Path) -> list[str]:
    """Return the arguments the stub `tar` recorded."""
    log = tmp_path / "stubs" / "tar-args.log"
    assert log.is_file(), "the extract step did not invoke tar"
    return log.read_text(encoding="utf-8").splitlines()


def test_gnu_tar_is_told_a_colon_is_part_of_the_name(tmp_path: Path) -> None:
    """A drive-letter path must never reach GNU tar as `host:path`.

    Without this the step fails with `Cannot connect to D: resolve failed`
    after the archive has been downloaded and verified.
    """
    result = _run_extract(tmp_path, staging_dir=WINDOWS_STAGING_DIR, gnu=True)

    assert result.returncode == 0, result.stderr
    arguments = _tar_arguments(tmp_path)
    archive = next(argument for argument in arguments if ASSET in argument)
    # Either defence is sufficient on its own, and both are present in CI:
    # the path arrives POSIX-normalized, or tar is told to treat the colon
    # literally.
    assert "--force-local" in arguments or ":" not in archive


def test_bsdtar_is_not_given_a_flag_it_rejects(tmp_path: Path) -> None:
    """Avoid a flag bsdtar rejects, since it ships on Windows and macOS.

    It exits non-zero on `--force-local`, so probing for GNU first is what
    keeps the step working on the runners that ship it.
    """
    result = _run_extract(tmp_path, staging_dir=WINDOWS_STAGING_DIR, gnu=False)

    assert result.returncode == 0, result.stderr
    assert "--force-local" not in _tar_arguments(tmp_path)


@pytest.mark.parametrize("gnu", [True, False])
def test_a_posix_staging_path_is_unaffected(tmp_path: Path, *, gnu: bool) -> None:
    """The ordinary case must keep working under either tar."""
    staging = tmp_path / "staging"
    result = _run_extract(tmp_path, staging_dir=str(staging), gnu=gnu)

    assert result.returncode == 0, result.stderr
    arguments = _tar_arguments(tmp_path)
    assert f"{staging}/{ASSET}" in arguments
    assert "--strip-components=1" in arguments


def test_the_resolve_step_normalizes_the_staging_path() -> None:
    """The path is normalized once, where it is first produced.

    Normalising in each consumer would leave the next one to remember; doing
    it at the source means download, verify, extract, and install all receive
    the POSIX form.
    """
    script = step_by_name("Resolve Whitaker release")["run"]
    assert isinstance(script, str)

    assert "cygpath -u" in script
    assert "WHITAKER_STAGING_DIR" in script
