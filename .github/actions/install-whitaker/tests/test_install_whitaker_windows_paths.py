r"""Tests for Windows path handling in the install-whitaker lifecycle.

`RUNNER_TEMP` is a native path, so under Git Bash the staging directory arrives
as `D:\a\_temp/whitaker-installer-release`. GNU tar reads a colon in an archive
path as rmt's `host:path` syntax and tries to resolve `D` as a hostname, which
fails after the archive has already been downloaded and verified.

Two defences, and these tests hold both: the path is normalized to POSIX form
where it is first produced, and GNU tar is additionally told to treat a colon
as part of the name. The extract fragment is executed under Bash against a stub
`tar` that records its arguments, so what is measured is the shipped step.

Since #446 the extractor is chosen by the asset's extension, so the tarball
arm is where `tar` is reached and where the colon defence lives. These tests
therefore drive the fragment with a tarball asset. The zip arm never invokes
`tar` at all, which is the point of the fix, and it is covered separately at
the foot of this module.
"""

from __future__ import annotations

import os
import pathlib as pl
import shutil
import subprocess
import typing as typ
import zipfile

import pytest
from _action_manifest import step_by_name
from _fragment_runner import require_posix_host
from hypothesis import given, settings
from hypothesis import strategies as st

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

require_posix_host()

#: The action's own zip extractor, used when no system bsdtar is available.
ZIP_SCRIPT = pl.Path(__file__).resolve().parents[1] / "scripts" / "extract-zip.py"

#: A staging directory in the shape Git Bash hands the action on Windows.
WINDOWS_STAGING_DIR = r"D:\a\_temp/whitaker-installer-release"


class Asset(typ.NamedTuple):
    """A release asset and the extension the extract step branches on."""

    name: str
    extension: str


#: A tarball asset, because the tarball arm is the one that reaches `tar` and
#: so the one the colon defence applies to.
TARBALL = Asset("whitaker-installer-x86_64-unknown-linux-gnu-v0.2.7.tar.gz", "tar.gz")
#: The Windows asset, whose arm must never reach `tar`.
ZIP = Asset("whitaker-installer-x86_64-pc-windows-msvc-v0.2.7.zip", "zip")
#: An asset whose format the step does not know, which must fail closed.
UNKNOWN = Asset("whitaker-installer-v0.2.7.rar", "rar")
ASSET = TARBALL.name


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
    tmp_path: Path,
    *,
    staging_dir: str,
    gnu: bool,
    asset: Asset = TARBALL,
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
        "WHITAKER_ASSET": asset.name,
        "WHITAKER_EXTENSION": asset.extension,
        "WHITAKER_NEEDS_INSTALL": "true",
        "WHITAKER_STAGING_DIR": staging_dir,
        "WHITAKER_INSTALLER_VERSION": "0.2.7",
        "WHITAKER_ZIP_SCRIPT": str(ZIP_SCRIPT),
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


def _run_resolve(
    tmp_path: Path, *, staging_dir: str, with_cygpath: bool
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the resolve fragment and return the process and the recorded path.

    Both `cygpath` and the resolve script are stubbed, so what is measured is
    the staging path the fragment hands downstream rather than its text.
    """
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - environment guard
        pytest.skip("bash not found on PATH")
    stub_dir = tmp_path / "cygpath-stub"
    stub_dir.mkdir(parents=True, exist_ok=True)
    if with_cygpath:
        # Stands in for the real cygpath: D:\a\_temp becomes /d/a/_temp.
        stub = stub_dir / "cygpath"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'python3 -c "import sys;p=sys.argv[1];'
            "print('/'+p[0].lower()+p[2:].replace(chr(92),'/'))\" \"$2\"\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    recorded = tmp_path / "staging-dir.txt"
    resolve_script = tmp_path / "resolve-release.sh"
    resolve_script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s" "$WHITAKER_STAGING_DIR" > "{recorded}"\n'
        'printf "status=install\\n"\n'
        'printf "staging-dir=%s\\n" "$WHITAKER_STAGING_DIR"\n',
        encoding="utf-8",
    )
    resolve_script.chmod(0o755)

    github_output = tmp_path / "github-output"
    github_output.touch()
    path = os.environ.get("PATH", "")
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{path}" if with_cygpath else path,
        "GITHUB_OUTPUT": str(github_output),
        "WHITAKER_INSTALLER_VERSION": "0.2.7",
        "WHITAKER_RESOLVE_SCRIPT": str(resolve_script),
        "WHITAKER_STAGING_DIR": staging_dir,
    }
    environment.pop("GITHUB_STEP_SUMMARY", None)
    script = step_by_name("Resolve Whitaker release")["run"]
    assert isinstance(script, str)
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [bash, "-c", script],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
        timeout=30,
    )
    seen = recorded.read_text(encoding="utf-8") if recorded.is_file() else ""
    return completed, seen


def test_the_resolve_step_hands_downstream_a_posix_path(tmp_path: Path) -> None:
    """The conversion must reach the resolve script, not merely be written.

    Asserting on the fragment's text would pass if `cygpath` ran without its
    result being assigned, or after the resolve script; in both cases the
    later steps would still receive the native path.
    """
    completed, seen = _run_resolve(
        tmp_path, staging_dir=WINDOWS_STAGING_DIR, with_cygpath=True
    )

    assert completed.returncode == 0, completed.stderr
    assert ":" not in seen, f"the drive letter survived: {seen}"
    assert seen.startswith("/d/")
    assert seen.endswith("/whitaker-installer-release")


def test_the_published_staging_path_is_the_converted_one(tmp_path: Path) -> None:
    """The value carried across the step boundary must be the converted one.

    Parsed rather than matched as a substring: `staging-dir=<seen>-suffix`
    contains `staging-dir=<seen>` while breaking the step-output contract, so
    a substring assertion would stay green through exactly that regression.
    """
    completed, seen = _run_resolve(
        tmp_path, staging_dir=WINDOWS_STAGING_DIR, with_cygpath=True
    )

    assert completed.returncode == 0, completed.stderr
    published = (tmp_path / "github-output").read_text(encoding="utf-8")
    records = [
        line.removeprefix("staging-dir=")
        for line in published.splitlines()
        if line.startswith("staging-dir=")
    ]

    assert records == [seen]


def test_a_posix_host_without_cygpath_is_left_alone(tmp_path: Path) -> None:
    """Linux and macOS runners have no `cygpath`, and need none."""
    staging = str(tmp_path / "staging")

    completed, seen = _run_resolve(tmp_path, staging_dir=staging, with_cygpath=False)

    assert completed.returncode == 0, completed.stderr
    assert seen == staging


#: Path shapes a runner may hand the action. The drive-letter forms are what
#: Git Bash produces from `RUNNER_TEMP`. The POSIX forms are written relative
#: so the test can create them; their shape, not their location, is what
#: distinguishes them here.
WINDOWS_PREFIXES = ("D:\\a\\_temp", "C:\\Users\\runneradmin\\AppData\\Local\\Temp")
POSIX_PREFIXES = ("work/_temp", "runner/work/_temp")
STAGING_PREFIXES = st.sampled_from(WINDOWS_PREFIXES + POSIX_PREFIXES)
STAGING_SUFFIXES = st.lists(
    st.sampled_from(["whitaker", "installer", "release", "a", "1"]),
    min_size=1,
    max_size=3,
).map("-".join)


@given(prefix=STAGING_PREFIXES, suffix=STAGING_SUFFIXES, gnu=st.booleans())
@settings(max_examples=40, derandomize=True, deadline=None)
def test_extraction_never_hands_tar_a_remote_looking_path(
    prefix: str, suffix: str, tmp_path_factory: object, *, gnu: bool
) -> None:
    """Across path shapes and both tars, extraction must succeed.

    The invariant is one thing: whatever the runner supplies, tar is never
    asked to open something it will read as `host:path`. Either the colon is
    gone, or GNU tar has been told to treat it literally, and bsdtar is never
    given the flag it rejects.
    """
    root = typ.cast("pytest.TempPathFactory", tmp_path_factory).mktemp("paths")
    if prefix in WINDOWS_PREFIXES:
        # A literal name under the working directory on a POSIX test host,
        # carrying the drive-letter colon that GNU tar would misread.
        staging = f"{prefix}\\{suffix}"
    else:
        staging = f"{root}/{prefix}/{suffix}"

    result = _run_extract(root, staging_dir=staging, gnu=gnu)

    assert result.returncode == 0, result.stderr
    arguments = _tar_arguments(root)
    archive = next(argument for argument in arguments if ASSET in argument)
    if gnu:
        assert "--force-local" in arguments or ":" not in archive
    else:
        assert "--force-local" not in arguments


def _write_zip_asset(staging: pl.Path, asset: str) -> None:
    """Write a release-shaped zip: one top-level directory, one executable."""
    staging.mkdir(parents=True, exist_ok=True)
    stem = asset.rsplit(".", 1)[0]
    with zipfile.ZipFile(staging / asset, "w") as package:
        package.writestr(f"{stem}/whitaker-installer.exe", b"installer")


def test_the_zip_arm_never_invokes_tar(tmp_path: Path) -> None:
    """The Windows asset must not be handed to tar at all.

    This is the regression for #446. Git Bash on a GitHub-hosted Windows
    runner puts MSYS2's GNU tar first on PATH, and GNU tar cannot read a zip,
    so any arm that reaches `tar` for a zip asset fails after the archive has
    already been downloaded and verified. The stub here is GNU tar, matching
    that runner, and it must never be called.
    """
    staging = tmp_path / "staging"
    _write_zip_asset(staging, ZIP.name)

    result = _run_extract(tmp_path, staging_dir=str(staging), gnu=True, asset=ZIP)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "stubs" / "tar-args.log").exists(), (
        "the zip asset was handed to tar"
    )
    extracted = sorted(path.name for path in (staging / "extract").iterdir())
    assert extracted == ["whitaker-installer.exe"]


def test_the_zip_arm_strips_the_top_level_directory(tmp_path: Path) -> None:
    """The zip arm must match `tar --strip-components=1`.

    The install step looks for the executable directly under the extract
    directory, so an arm that preserved the archive's top-level directory
    would extract successfully and then fail to find what it extracted.
    """
    staging = tmp_path / "staging"
    _write_zip_asset(staging, ZIP.name)

    result = _run_extract(tmp_path, staging_dir=str(staging), gnu=True, asset=ZIP)

    assert result.returncode == 0, result.stderr
    installer = staging / "extract" / "whitaker-installer.exe"
    assert installer.is_file()
    assert installer.read_bytes() == b"installer"


def test_an_unknown_extension_is_refused(tmp_path: Path) -> None:
    """An unrecognized extension must fail rather than fall through to tar.

    Falling through is precisely how a zip reached GNU tar, so the default arm
    stops instead of guessing at the format.
    """
    staging = tmp_path / "staging"
    _write_zip_asset(staging, UNKNOWN.name)

    result = _run_extract(tmp_path, staging_dir=str(staging), gnu=True, asset=UNKNOWN)

    assert result.returncode != 0
    assert "unsupported archive extension" in result.stderr
    assert not (tmp_path / "stubs" / "tar-args.log").exists()
