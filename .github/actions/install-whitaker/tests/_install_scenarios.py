"""Build deterministic fixtures for the install-whitaker lifecycle fragments.

A scenario describes one runner, one cache state, and one trust-anchor
configuration. Running it executes the real input-validation and lifecycle
fragments against a stubbed ``curl`` that serves a real release archive built
for that runner, so no network access is needed but extraction is genuine: the
runner's own ``tar`` unpacks the gzip fixtures, and the zip fixture is unpacked
with real zip semantics including ``--strip-components``. A failure to strip the
archive's top-level directory therefore fails the suite.
"""

from __future__ import annotations

import dataclasses as dc
import gzip
import hashlib
import io
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

from _action_manifest import (
    DIGEST_MANIFEST_NAME,
    RESOLVE_SCRIPT_PATH,
    SUPPORTED_PLATFORMS,
    ZIP_SCRIPT_PATH,
    asset_name,
    installer_filename,
    lifecycle_steps,
    step_by_id,
    step_by_name,
)

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    LifecycleResult,
    StepResult,
    ambient_env,
    bash_file_path,
    bash_path,
    run_lifecycle,
    run_step,
)

#: Marker selecting a digest derived from the scenario's archive payload.
AUTO = "auto"

_CURL_STUB = """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = --version ]; then
  printf 'curl %s (x86_64-test) libcurl/%s\\n' "$STUB_CURL_VERSION" "$STUB_CURL_VERSION"
  exit 0
fi
printf '%s\\n' "$*" >> "$DOWNLOAD_LOG"
if [ "$FAIL_DOWNLOAD" = "true" ]; then
  echo "Whitaker release download failed" >&2
  exit 31
fi
output=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ "$url" == *.sha256 ]]; then
  printf '%s  archive\\n' "$SIDECAR_SHA256" > "$output"
else
  cp -- "$ARCHIVE_FIXTURE" "$output"
fi
printf '200 %s 0.123 0' "$(wc -c < "$output" | tr -d ' ')"
"""

_INSTALLER_STUB = """#!/usr/bin/env bash
set -euo pipefail
if [ "$FAIL_INSTALLER" = "true" ]; then
  echo "whitaker-installer failed while installing the Dylint suite" >&2
  exit 33
fi
printf '%s\\n' "suite installed" >> "$INSTALLER_LOG"
"""

_CONFLICTING_INSTALLER_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "ambient installer ran" >> "$CONFLICT_LOG"
"""


_TAR_SHIM_WRAPPER = """#!/usr/bin/env bash
# Runs the shim body through whichever Python is first on PATH. Naming the
# interpreter by absolute path does not survive a Git Bash host, where
# `sys.executable` lives under a directory containing a space; the harness
# instead puts the interpreter's directory on the PATH it builds, so the
# command word here is a bare name.
set -euo pipefail
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$candidate" "{script}" "$@"
  fi
done
echo "the tar shim found no Python interpreter on PATH" >&2
exit 127
"""

_TAR_SHIM = r'''
"""Stand in for the runner's tar, recording arguments and refusing zip.

Faithful to the tool the action actually meets. On every runner this action
supports, the `tar` first on PATH is GNU tar: that is true of the Linux and
macOS images, and it is true of a GitHub-hosted Windows runner too, because the
extract step's `shell: bash` is Git Bash and Git Bash puts MSYS2's tar ahead of
the Windows system directory. GNU tar cannot read a zip.

An earlier version of this shim unpacked zip archives itself, which made the
harness's `tar` zip-capable and hid issue #446 for as long as it existed. The
shim now refuses a zip exactly as GNU tar does, so the action has to reach its
zip arm to succeed, and delegates everything else to the real tar so gzip
extraction stays genuine.
"""

from __future__ import annotations

import os
import pathlib
import sys
import zipfile

argv = sys.argv[1:]
with pathlib.Path(os.environ["EXTRACT_LOG"]).open("a", encoding="utf-8") as log:
    log.write(" ".join(argv) + "\n")

archive: pathlib.Path | None = None
index = 0
while index < len(argv):
    if argv[index] == "-xf":
        archive = pathlib.Path(argv[index + 1])
        index += 2
    else:
        index += 1

if archive is not None and zipfile.is_zipfile(archive):
    sys.stderr.write("tar: This does not look like a tar archive\n")
    sys.stderr.write("tar: Exiting with failure status due to previous errors\n")
    sys.exit(2)

real_tar = os.environ["REAL_TAR"]
os.execv(real_tar, [real_tar, *argv])
'''

_FORBIDDEN_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$0 $*" >> "$FORBIDDEN_LOG"
echo "$0 is not available on every supported runner" >&2
exit 127
"""


def _archive_member(scenario: InstallScenario) -> str:
    """Return the single member path the release archive carries."""
    stem = scenario.asset.rsplit(".", 1)[0]
    return f"{stem}/{scenario.installer_name}"


def _tar_fixture(member: str, payload: bytes) -> bytes:
    """Build a byte-stable gzip tar archive holding one executable member."""
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as package,
    ):
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        info.mode = 0o755
        info.mtime = 0
        package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _zip_fixture(member: str, payload: bytes) -> bytes:
    """Build a byte-stable zip archive holding one executable member."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o755 << 16
        package.writestr(info, payload)
    return buffer.getvalue()


def archive_fixture(scenario: InstallScenario) -> bytes:
    """Return the release archive the stubbed download serves for ``scenario``."""
    member = _archive_member(scenario)
    payload = _INSTALLER_STUB.encode()
    if scenario.asset.endswith(".zip"):
        return _zip_fixture(member, payload)
    return _tar_fixture(member, payload)


@dc.dataclass(frozen=True)
class InstallScenario:
    """Describe one lifecycle run of the install-whitaker action."""

    runner_os: str = "Linux"
    runner_arch: str = "X64"
    installer_present: bool = False
    fail_download: bool = False
    fail_installer: bool = False
    installer_version: str = "0.2.7"
    cargo_home_name: str = "cargo-home"
    cargo_home_value: str | None = None
    cache_hit: bool = False
    cache_provider: str = "github"
    conflicting_installer: bool = False
    curl_version: str = "8.15.0"
    cached_version: str | None = None
    version_marker: bool = True
    sidecar_sha256: str = AUTO
    pinned_sha256: str | None = AUTO
    installer_sha256: str = ""

    @property
    def payload_sha256(self) -> str:
        """Return the digest of the archive the ``curl`` stub serves."""
        return hashlib.sha256(archive_fixture(self)).hexdigest()

    @property
    def expected_sidecar(self) -> str:
        """Return the digest the ``curl`` stub writes to the sidecar."""
        if self.sidecar_sha256 == AUTO:
            return self.payload_sha256
        return self.sidecar_sha256

    @property
    def expected_pinned(self) -> str | None:
        """Return the digest written into the test-local manifest."""
        if self.pinned_sha256 == AUTO:
            return self.payload_sha256
        return self.pinned_sha256

    @property
    def asset(self) -> str:
        """Return the release asset this runner pair selects."""
        return asset_name(self.runner_os, self.runner_arch, self.installer_version)

    @property
    def installer_name(self) -> str:
        """Return the installer filename this runner pair installs."""
        return installer_filename(self.runner_os, self.runner_arch)


@dc.dataclass(frozen=True)
class InstallRun:
    """Expose the fixture paths and result of one executed scenario."""

    result: LifecycleResult
    context: ActionContext
    root: Path
    cargo_home: Path
    staging_dir: Path

    @property
    def installer_path(self) -> Path:
        """Return the installer path the validation fragment resolved."""
        return Path(self.context.step_outputs["validate-inputs"]["installer-path"])

    @property
    def download_log(self) -> Path:
        """Return the log of stubbed ``curl`` invocations."""
        return self.root / "download.log"

    @property
    def installer_log(self) -> Path:
        """Return the log written by the stubbed installer."""
        return self.root / "installer.log"

    @property
    def conflict_log(self) -> Path:
        """Return the log written by an ambient installer on ``PATH``."""
        return self.root / "conflict.log"

    @property
    def version_marker(self) -> Path:
        """Return the marker recording which version the cache holds."""
        return self.installer_path.parent / ".whitaker-installer-version"

    def resolution_record(self) -> list[str]:
        """Return the record the resolution step published for publication."""
        return [
            line
            for line in self.context.step_outputs.get("resolve-release", {})
            .get("resolution", "")
            .splitlines()
            if line
        ]

    @property
    def extract_log(self) -> Path:
        """Return the log of stubbed ``tar`` invocations."""
        return self.root / "extract.log"

    @property
    def forbidden_log(self) -> Path:
        """Return the log of commands the action must never invoke."""
        return self.root / "forbidden.log"

    @property
    def summary(self) -> Path:
        """Return the job-summary file the fragments append metrics to."""
        return self.root / "summary.md"

    def summary_lines(self) -> list[str]:
        """Return the emitted job-summary metrics."""
        if not self.summary.exists():
            return []
        return self.summary.read_text(encoding="utf-8").splitlines()

    def lifecycle_metrics(self) -> list[str]:
        """Return the job-summary metrics excluding per-transfer telemetry."""
        return [line for line in self.summary_lines() if ".transfer." not in line]

    def transfer_metrics(self) -> list[str]:
        """Return the per-transfer telemetry metrics."""
        return [line for line in self.summary_lines() if ".transfer." in line]

    def published_output_lines(self) -> list[str]:
        """Return every ``$GITHUB_OUTPUT`` line written after validation."""
        outputs = self.root / "outputs"
        if not outputs.is_dir():
            return []
        return [
            line
            for path in sorted(outputs.iterdir())
            if path.name != "validate-output"
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]


def _prepare_action_directory(root: Path, scenario: InstallScenario) -> Path:
    """Create the action directory the fragments read at ``github.action_path``."""
    action_path = root / "action"
    (action_path / "scripts").mkdir(parents=True, exist_ok=True)
    write_digest_manifest(action_path / DIGEST_MANIFEST_NAME, scenario)
    for source in (RESOLVE_SCRIPT_PATH, ZIP_SCRIPT_PATH):
        destination = action_path / "scripts" / source.name
        shutil.copy2(source, destination)
        destination.chmod(0o755)
    return action_path


def _prepare_cargo_home(root: Path, scenario: InstallScenario) -> Path:
    """Create the Cargo home, seeding any cached installer and its marker."""
    cargo_home = root / scenario.cargo_home_name
    cargo_home.mkdir(parents=True, exist_ok=True)
    if not scenario.installer_present:
        return cargo_home
    _write_executable(cargo_home / "bin" / scenario.installer_name, _INSTALLER_STUB)
    if scenario.version_marker:
        marker_version = scenario.cached_version or scenario.installer_version
        (cargo_home / "bin" / ".whitaker-installer-version").write_text(
            f"{marker_version}\n",
            encoding="utf-8",
        )
    return cargo_home


def _build_context(
    root: Path,
    scenario: InstallScenario,
    cargo_home: Path,
    action_path: Path,
) -> ActionContext:
    """Build the expression context the lifecycle fragments resolve against."""
    return ActionContext(
        inputs={
            "cache-provider": scenario.cache_provider,
            "cargo-home": scenario.cargo_home_value or bash_path(cargo_home),
            "installer-sha256": scenario.installer_sha256,
            "installer-version": scenario.installer_version,
        },
        runner_os=scenario.runner_os,
        runner_arch=scenario.runner_arch,
        action_path=bash_path(action_path),
        runner_temp=bash_path(root / "runner-temp"),
        step_outputs={
            "cache-whitaker-installer": {
                "cache-hit": str(scenario.cache_hit).lower(),
            },
        },
    )


def _posix_path(path: str) -> str:
    """Return a path Bash can execute, with forward separators."""
    return path.replace("\\", "/")


def _real_tar() -> str:
    """Return the ambient ``tar`` the shim delegates gzip extraction to."""
    resolved = shutil.which("tar")
    if resolved is None:  # pragma: no cover - every supported host ships tar.
        message = "tar is required to exercise the extraction fragment"
        raise RuntimeError(message)
    return resolved


def _write_executable(path: Path, content: str) -> None:
    """Write an executable test stub."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def write_digest_manifest(path: Path, scenario: InstallScenario) -> None:
    """Write the test-local pinned digest manifest for ``scenario``."""
    pinned = scenario.expected_pinned
    if pinned is None:
        path.write_text("# no pinned digests\n", encoding="utf-8")
        return
    entries = sorted(
        {
            asset_name(*pair.split(":"), scenario.installer_version)
            for pair in SUPPORTED_PLATFORMS
        },
    )
    path.write_text(
        "".join(f"{pinned}  {entry}\n" for entry in entries),
        encoding="utf-8",
    )


def _prepare_stubs(root: Path, scenario: InstallScenario) -> str:
    """Create the command stubs and return the ``PATH`` for the fragments."""
    stub_bin = root / "stub-bin"
    _write_executable(stub_bin / "curl", _CURL_STUB)
    shim_body = stub_bin / "tar-shim.py"
    shim_body.parent.mkdir(parents=True, exist_ok=True)
    shim_body.write_text(_TAR_SHIM, encoding="utf-8")
    _write_executable(
        stub_bin / "tar",
        _TAR_SHIM_WRAPPER.format(script=_posix_path(str(shim_body))),
    )
    # `unzip` stays forbidden: the action must never require it. The zip arm
    # reaches the Windows system tar on a real Windows runner and the action's
    # own Python extractor everywhere else, and neither is `unzip`.
    _write_executable(stub_bin / "unzip", _FORBIDDEN_STUB)
    # `bash_path` yields the form Bash understands. A Windows drive path such
    # as `C:/Program Files/...` cannot go into a colon-separated PATH, because
    # the drive colon splits the entry in two.
    interpreter_dir = bash_path(Path(sys.executable).parent)
    path = f"{bash_path(stub_bin)}:{interpreter_dir}:/usr/bin:/bin"
    if scenario.conflicting_installer:
        ambient_bin = root / "ambient-bin"
        _write_executable(
            ambient_bin / "whitaker-installer",
            _CONFLICTING_INSTALLER_STUB,
        )
        path = f"{bash_path(ambient_bin)}:{path}"
    return path


def _base_env(root: Path, scenario: InstallScenario, path: str) -> dict[str, str]:
    """Return the ambient environment shared by every fragment."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    runner_temp = root / "runner-temp"
    runner_temp.mkdir(parents=True, exist_ok=True)
    return {
        **ambient_env(),
        "PATH": path,
        "ARCHIVE_FIXTURE": bash_file_path(root / "fixture-archive"),
        "CONFLICT_LOG": bash_file_path(root / "conflict.log"),
        "DOWNLOAD_LOG": bash_file_path(root / "download.log"),
        "EXPECTED_INSTALLER_NAME": scenario.installer_name,
        "EXTRACT_LOG": bash_file_path(root / "extract.log"),
        "FORBIDDEN_LOG": bash_file_path(root / "forbidden.log"),
        "REAL_TAR": _real_tar(),
        "FAIL_DOWNLOAD": str(scenario.fail_download).lower(),
        "FAIL_INSTALLER": str(scenario.fail_installer).lower(),
        "GITHUB_STEP_SUMMARY": bash_file_path(root / "summary.md"),
        "HOME": bash_path(home),
        "INSTALLER_LOG": bash_file_path(root / "installer.log"),
        "RUNNER_OS": scenario.runner_os,
        "RUNNER_TEMP": bash_path(runner_temp),
        "SIDECAR_SHA256": scenario.expected_sidecar,
        "STUB_CURL_VERSION": scenario.curl_version,
    }


def run_named_steps(
    root: Path,
    scenario: InstallScenario,
    step_names: list[str],
) -> InstallRun:
    """Run validation and only the named lifecycle fragments for ``scenario``."""
    return _run_scenario(root, scenario, [step_by_name(name) for name in step_names])


def run_install_scenario(root: Path, scenario: InstallScenario) -> InstallRun:
    """Run the validation and every lifecycle fragment for ``scenario``."""
    return _run_scenario(root, scenario, lifecycle_steps())


def _run_scenario(
    root: Path,
    scenario: InstallScenario,
    steps: list[dict[str, object]],
) -> InstallRun:
    """Run the validation fragment and then ``steps`` for ``scenario``."""
    root.mkdir(parents=True, exist_ok=True)
    cargo_home = _prepare_cargo_home(root, scenario)
    action_path = _prepare_action_directory(root, scenario)
    (root / "fixture-archive").write_bytes(archive_fixture(scenario))
    base_env = _base_env(root, scenario, _prepare_stubs(root, scenario))
    context = _build_context(root, scenario, cargo_home, action_path)
    environment = FragmentEnvironment(
        base_env=base_env,
        cwd=root,
        output_dir=root / "outputs",
    )
    validation = run_step(
        step_by_id("validate-inputs"),
        context,
        environment,
        "validate-output",
    )
    if validation.returncode != 0:
        result = LifecycleResult(
            steps=(StepResult("Validate Whitaker inputs", validation),),
        )
        return InstallRun(
            result=result,
            context=context,
            root=root,
            cargo_home=cargo_home,
            staging_dir=root / "runner-temp" / "whitaker-installer-release",
        )
    result = run_lifecycle(steps, context, environment)
    return InstallRun(
        result=result,
        context=context,
        root=root,
        cargo_home=cargo_home,
        staging_dir=root / "runner-temp" / "whitaker-installer-release",
    )
