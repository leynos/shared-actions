"""Build deterministic fixtures for the install-whitaker lifecycle fragments.

A scenario describes one runner, one cache state, and one trust-anchor
configuration. Running it executes the real input-validation and lifecycle
fragments against stubbed ``curl``, ``tar``, and ``unzip`` commands, so no
network access or real release archive is required.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
from pathlib import Path

from _action_manifest import (
    DIGEST_MANIFEST_NAME,
    SUPPORTED_PLATFORMS,
    asset_name,
    installer_filename,
    lifecycle_steps,
    step_by_id,
)
from _fragment_runner import (
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
  printf '%s' "$ARCHIVE_PAYLOAD" > "$output"
fi
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


_TAR_STUB = f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$EXTRACT_LOG"
extract_dir=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -C) extract_dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat > "$extract_dir/$EXPECTED_INSTALLER_NAME" <<'INSTALLER'
{_INSTALLER_STUB}INSTALLER
chmod +x "$extract_dir/$EXPECTED_INSTALLER_NAME"
"""

_FORBIDDEN_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$0 $*" >> "$FORBIDDEN_LOG"
echo "$0 is not available on every supported runner" >&2
exit 127
"""


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
    archive_payload: str = "payload"
    sidecar_sha256: str = AUTO
    pinned_sha256: str | None = AUTO
    installer_sha256: str = ""

    @property
    def payload_sha256(self) -> str:
        """Return the digest of the archive the ``curl`` stub serves."""
        return hashlib.sha256(self.archive_payload.encode()).hexdigest()

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
    _write_executable(stub_bin / "tar", _TAR_STUB)
    _write_executable(stub_bin / "unzip", _FORBIDDEN_STUB)
    path = f"{bash_path(stub_bin)}:/usr/bin:/bin"
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
        "ARCHIVE_PAYLOAD": scenario.archive_payload,
        "CONFLICT_LOG": bash_file_path(root / "conflict.log"),
        "DOWNLOAD_LOG": bash_file_path(root / "download.log"),
        "EXPECTED_INSTALLER_NAME": scenario.installer_name,
        "EXTRACT_LOG": bash_file_path(root / "extract.log"),
        "FORBIDDEN_LOG": bash_file_path(root / "forbidden.log"),
        "FAIL_DOWNLOAD": str(scenario.fail_download).lower(),
        "FAIL_INSTALLER": str(scenario.fail_installer).lower(),
        "GITHUB_STEP_SUMMARY": bash_file_path(root / "summary.md"),
        "HOME": bash_path(home),
        "INSTALLER_LOG": bash_file_path(root / "installer.log"),
        "RUNNER_OS": scenario.runner_os,
        "RUNNER_TEMP": bash_path(runner_temp),
        "SIDECAR_SHA256": scenario.expected_sidecar,
    }


def run_install_scenario(root: Path, scenario: InstallScenario) -> InstallRun:
    """Run the validation and lifecycle fragments for ``scenario``."""
    root.mkdir(parents=True, exist_ok=True)
    cargo_home = root / scenario.cargo_home_name
    cargo_home.mkdir(parents=True, exist_ok=True)
    action_path = root / "action"
    action_path.mkdir(parents=True, exist_ok=True)
    write_digest_manifest(action_path / DIGEST_MANIFEST_NAME, scenario)

    if scenario.installer_present:
        _write_executable(
            cargo_home / "bin" / scenario.installer_name,
            _INSTALLER_STUB,
        )

    path = _prepare_stubs(root, scenario)
    base_env = _base_env(root, scenario, path)
    context = ActionContext(
        inputs={
            "cache-provider": scenario.cache_provider,
            "cargo-home": scenario.cargo_home_value or bash_path(cargo_home),
            "installer-sha256": scenario.installer_sha256,
            "installer-version": scenario.installer_version,
        },
        runner_os=scenario.runner_os,
        runner_arch=scenario.runner_arch,
        action_path=bash_path(action_path),
        step_outputs={
            "cache-whitaker-installer": {
                "cache-hit": str(scenario.cache_hit).lower(),
            },
        },
    )
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
    result = run_lifecycle(lifecycle_steps(), context, environment)
    return InstallRun(
        result=result,
        context=context,
        root=root,
        cargo_home=cargo_home,
        staging_dir=root / "runner-temp" / "whitaker-installer-release",
    )
