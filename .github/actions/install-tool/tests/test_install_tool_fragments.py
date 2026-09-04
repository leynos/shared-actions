"""Behavioural tests: run the shipped fragments, do not read them.

An assertion on a fragment's text passes when the command it names runs
without its result being used, or runs in the wrong order, and in both cases
every later step still gets the wrong answer. So each fragment is written to a
file and executed as `bash <file>`, which is how the runner invokes it, against
archives built here.

The archives are real tarballs and zips containing a stub binary that prints a
version, because the two things most likely to break are the extractor's
handling of an archive shape and the version probe's handling of a tool that
does not answer `--version`.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
import typing as typ
import zipfile
from pathlib import Path

import pytest
from _fragment import Context, Result, run_step
from _manifest import ACTION_DIR, step_by_name

pytestmark = pytest.mark.skipif(
    not Path("/bin/sh").exists(), reason="POSIX shell required"
)

STUB = """#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  echo "widget 1.2.3"
  exit 0
fi
echo "widget: unexpected argument ${1:-}" >&2
exit 1
"""


def _stub_binary(tmp_path: Path, name: str = "widget") -> Path:
    path = tmp_path / name
    path.write_text(STUB, encoding="utf-8")
    path.chmod(0o755)
    return path


def _tarball(tmp_path: Path, member: str, binary: Path) -> Path:
    """Build a tar.gz placing the stub at a given member path."""
    archive = tmp_path / "widget.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(binary, arcname=member)
    return archive


def _zip(tmp_path: Path, member: str, binary: Path) -> Path:
    """Build a zip placing the stub at a given member path."""
    archive = tmp_path / "widget.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo(member)
        # The executable bit lives in the high bits of external_attr, and a
        # zip that loses it installs a file nothing can run.
        info.external_attr = 0o755 << 16
        handle.writestr(info, binary.read_text(encoding="utf-8"))
    return archive


def _digest(path: Path) -> str:
    """Return the archive's digest, or a placeholder when there is no archive.

    Several tests exercise steps that never look at one, and giving them a
    real file only to ignore it would obscure which input each is about.
    """
    if not path.is_file():
        return "0" * 64
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: A curl that copies a local file instead of fetching one. The action passes
#: `--proto '=https'`, which refuses a `file://` URL, and that guard is worth
#: keeping, so the transport is stubbed rather than the URL weakened.
CURL_STUB = """#!/usr/bin/env bash
set -euo pipefail
destination=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == "-o" ]]; then
    destination="$argument"
  fi
  previous="$argument"
done
if [[ -n "${CURL_STUB_EXIT:-}" && "${CURL_STUB_EXIT}" != 0 ]]; then
  echo "curl: stubbed failure" >&2
  exit "${CURL_STUB_EXIT}"
fi
cp "${CURL_STUB_SOURCE}" "$destination"
"""


def _curl_stub(tmp_path: Path) -> Path:
    """Return a directory holding the stub, to be put first on PATH."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "curl"
    stub.write_text(CURL_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return stub_dir


def _source_dir(tmp_path: Path) -> Path:
    """Return a directory to build the stub in, away from the archives."""
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    return source


def _context(tmp_path: Path, **overrides: dict[str, str]) -> Context:
    context = Context(
        inputs={"tool": "widget", "version": "1.2.3", "bin-dir": str(tmp_path / "bin")},
        runner={"os": "Linux", "arch": "X64", "temp": str(tmp_path / "temp")},
        github={"action_path": str(ACTION_DIR)},
        steps={},
    )
    (tmp_path / "temp").mkdir(parents=True, exist_ok=True)
    for key, value in overrides.items():
        getattr(context, key).update(value)
    return context


def _resolved(
    archive: Path, member: str, extension: str, **overrides: str
) -> dict[str, str]:
    resolved = {
        "triple": "x86_64-unknown-linux-gnu",
        "url": archive.as_uri(),
        "sha256": _digest(archive),
        "member": member,
        "sidecar-verified": "true",
        "extension": extension,
        "binary": "widget",
        "version-args": "--version",
        "version-check": "true",
        "expected-version": "widget 1.2.3",
    }
    resolved.update(overrides)
    return resolved


class Archive(typ.NamedTuple):
    """An archive and where the binary sits inside it."""

    path: Path
    member: str
    extension: str


def _install(
    tmp_path: Path, archive: Archive, **overrides: str
) -> tuple[Result, Result]:
    """Run probe, download and install against a local archive."""
    context = _context(tmp_path)
    context.steps["resolve"] = _resolved(
        archive.path, archive.member, archive.extension, **overrides
    )

    probe = run_step(
        step_by_name("Probe for an installed tool"), context, tmp_path / "s1"
    )
    assert probe.returncode == 0, probe.stderr
    context.steps["probe"] = probe.outputs

    stub_dir = _curl_stub(tmp_path)
    download = run_step(
        step_by_name("Download and verify the archive"),
        context,
        tmp_path / "s2",
        extra_env={
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}",
            "CURL_STUB_SOURCE": str(archive.path),
            "CURL_STUB_EXIT": "0" if archive.path.is_file() else "22",
        },
    )
    if download.returncode != 0:
        return probe, download
    context.steps["download"] = download.outputs

    install = run_step(
        step_by_name("Extract and install the binary"), context, tmp_path / "s3"
    )
    return probe, install


class TestProbe:
    """What the probe decides, and on what evidence."""

    def test_reports_a_miss_when_nothing_is_installed(self, tmp_path: Path) -> None:
        """The ordinary first call."""
        context = _context(tmp_path)
        context.steps["resolve"] = _resolved(
            tmp_path / "unused.tar.gz", "widget", "tar.gz"
        )

        result = run_step(
            step_by_name("Probe for an installed tool"), context, tmp_path / "s"
        )

        assert result.returncode == 0, result.stderr
        assert result.metrics["install-tool.cache"] == "miss"
        assert result.outputs["needs-install"] == "true"
        assert result.outputs["cache-hit"] == "false"

    def test_reports_a_hit_for_the_exact_version(self, tmp_path: Path) -> None:
        """A hit is what makes a second call cheap."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub_binary(bin_dir)
        context = _context(tmp_path)
        context.steps["resolve"] = _resolved(
            tmp_path / "unused.tar.gz", "widget", "tar.gz"
        )

        result = run_step(
            step_by_name("Probe for an installed tool"), context, tmp_path / "s"
        )

        assert result.metrics["install-tool.cache"] == "hit"
        assert result.outputs["needs-install"] == "false"
        assert result.outputs["cache-hit"] == "true"

    def test_reports_stale_for_the_wrong_version(self, tmp_path: Path) -> None:
        """The failure this action exists to prevent, seen from the probe.

        A binary of the right name and the wrong version is worse than none,
        because everything downstream believes it.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub_binary(bin_dir)
        context = _context(tmp_path)
        resolved = _resolved(tmp_path / "unused.tar.gz", "widget", "tar.gz")
        resolved["expected-version"] = "widget 9.9.9"
        context.steps["resolve"] = resolved

        result = run_step(
            step_by_name("Probe for an installed tool"), context, tmp_path / "s"
        )

        assert result.metrics["install-tool.cache"] == "stale"
        assert result.outputs["needs-install"] == "true"

    def test_takes_an_unaskable_tool_on_trust(self, tmp_path: Path) -> None:
        """dylint-link cannot be asked, and reinstalling it every run is waste.

        Recorded as its own outcome so the series never claims a verified hit
        it did not make.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub_binary(bin_dir)
        context = _context(tmp_path)
        resolved = _resolved(tmp_path / "unused.tar.gz", "widget", "tar.gz")
        resolved["version-check"] = "false"
        resolved["version-args"] = ""
        context.steps["resolve"] = resolved

        result = run_step(
            step_by_name("Probe for an installed tool"), context, tmp_path / "s"
        )

        assert result.metrics["install-tool.cache"] == "hit-unverified"
        assert result.outputs["needs-install"] == "false"

    def test_adds_the_directory_to_the_path(self, tmp_path: Path) -> None:
        """A tool nobody can find is not installed for practical purposes."""
        context = _context(tmp_path)
        context.steps["resolve"] = _resolved(
            tmp_path / "unused.tar.gz", "widget", "tar.gz"
        )

        result = run_step(
            step_by_name("Probe for an installed tool"), context, tmp_path / "s"
        )

        assert result.path_additions == [str(tmp_path / "bin")]


class TestDownloadAndVerify:
    """The digest is the trust anchor, so the failure path matters most."""

    def test_installs_from_a_tarball_under_a_directory(self, tmp_path: Path) -> None:
        """The shape cargo-audit, dylint and sccache use."""
        binary = _stub_binary(_source_dir(tmp_path))
        archive = _tarball(tmp_path, "widget-1.2.3/widget", binary)

        _probe, install = _install(
            tmp_path, Archive(archive, "widget-1.2.3/widget", "tar.gz")
        )

        assert install.returncode == 0, install.stderr
        assert install.metrics["install-tool.install"] == "ok"
        assert (tmp_path / "bin" / "widget").is_file()

    def test_installs_from_a_tarball_with_the_binary_at_the_root(
        self, tmp_path: Path
    ) -> None:
        """The shape cargo-nextest and cargo-llvm-cov use.

        `--strip-components=1` is right for the other shape and destroys this
        one, which is why the manifest names the member instead.
        """
        binary = _stub_binary(_source_dir(tmp_path))
        archive = _tarball(tmp_path, "widget", binary)

        _probe, install = _install(tmp_path, Archive(archive, "widget", "tar.gz"))

        assert install.returncode == 0, install.stderr
        assert (tmp_path / "bin" / "widget").is_file()

    def test_installs_from_a_zip(self, tmp_path: Path) -> None:
        """Chosen by extension, never by probing what `tar` resolves to."""
        binary = _stub_binary(_source_dir(tmp_path))
        archive = _zip(tmp_path, "widget", binary)

        _probe, install = _install(tmp_path, Archive(archive, "widget", "zip"))

        assert install.returncode == 0, install.stderr
        assert (tmp_path / "bin" / "widget").is_file()

    def test_refuses_an_archive_whose_digest_disagrees(self, tmp_path: Path) -> None:
        """Whatever arrived is not what was pinned, so nothing is installed."""
        binary = _stub_binary(_source_dir(tmp_path))
        archive = _tarball(tmp_path, "widget", binary)

        _probe, download = _install(
            tmp_path, Archive(archive, "widget", "tar.gz"), sha256="1" * 64
        )

        assert download.returncode != 0
        assert download.metrics["install-tool.digest"] == "mismatch"
        assert "digest mismatch" in download.stderr
        assert not (tmp_path / "bin" / "widget").exists()

    def test_reports_a_download_that_never_arrived(self, tmp_path: Path) -> None:
        """A missing archive is not a digest failure and is not reported as one."""
        missing = tmp_path / "absent.tar.gz"

        _probe, download = _install(tmp_path, Archive(missing, "widget", "tar.gz"))

        assert download.returncode != 0
        assert download.metrics["install-tool.download"] == "failed"
        assert "install-tool.digest" not in download.metrics

    def test_refuses_an_archive_missing_its_member(self, tmp_path: Path) -> None:
        """A verified archive that does not contain the tool is still a failure."""
        binary = _stub_binary(_source_dir(tmp_path))
        archive = _tarball(tmp_path, "somewhere-else/widget", binary)

        _probe, install = _install(
            tmp_path, Archive(archive, "widget-1.2.3/widget", "tar.gz")
        )

        assert install.returncode != 0
        assert install.metrics["install-tool.install"] == "missing-member"


class TestVerification:
    """The installed binary has the last word."""

    def _verified(self, tmp_path: Path, **overrides: str) -> Result:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        _stub_binary(bin_dir)
        context = _context(tmp_path)
        resolved = _resolved(
            tmp_path / "unused.tar.gz", "widget", "tar.gz", **overrides
        )
        context.steps["resolve"] = resolved
        context.steps["probe"] = {
            "path": str(bin_dir / "widget"),
            "cache-hit": overrides.pop("cache-hit", "false"),
        }
        return run_step(
            step_by_name("Verify the installed tool"), context, tmp_path / "v"
        )

    def test_accepts_the_version_it_asked_for(self, tmp_path: Path) -> None:
        """The ordinary case, and the one worth being sure of."""
        result = self._verified(tmp_path)

        assert result.returncode == 0, result.stderr
        assert result.metrics["install-tool.verify"] == "ok"
        assert result.metrics["install-tool.result"] == "installed"

    def test_refuses_a_binary_reporting_another_version(self, tmp_path: Path) -> None:
        """A digest proves the bytes; only running it proves the tool."""
        result = self._verified(tmp_path, **{"expected-version": "widget 9.9.9"})

        assert result.returncode != 0
        assert result.metrics["install-tool.verify"] == "mismatch"

    def test_records_a_tool_it_cannot_ask(self, tmp_path: Path) -> None:
        """Silence here would look identical to a passing check."""
        result = self._verified(
            tmp_path, **{"version-check": "false", "version-args": ""}
        )

        assert result.returncode == 0, result.stderr
        assert result.metrics["install-tool.verify"] == "unsupported"

    def test_fails_when_nothing_was_installed(self, tmp_path: Path) -> None:
        """The path exists in the outputs but not on disk."""
        context = _context(tmp_path)
        context.steps["resolve"] = _resolved(
            tmp_path / "unused.tar.gz", "widget", "tar.gz"
        )
        context.steps["probe"] = {
            "path": str(tmp_path / "bin" / "widget"),
            "cache-hit": "false",
        }

        result = run_step(
            step_by_name("Verify the installed tool"), context, tmp_path / "v"
        )

        assert result.returncode != 0
        assert result.metrics["install-tool.verify"] == "missing"
