"""Verify the pinned, checksum-verified install-yaml-lint action contract."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import typing as typ
from pathlib import Path

import pytest
import yaml

# The action supports Linux and macOS only, and its tests drive the script
# through POSIX shell stubs, so they have nothing to say on a Windows runner.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX shell stubs; the action is Linux/macOS only"
)

ACTION_PATH = Path(__file__).resolve().parents[1] / "action.yml"
_LINUX_X64_DIGESTS = {
    "actionlint": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    "shellcheck": "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198",
}


def _script() -> str:
    """Return the action's single installation shell fragment."""
    steps = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]
    assert len(steps) == 1, "install-yaml-lint should have one atomic install step"
    run_script = steps[0].get("run")
    assert isinstance(run_script, str), "install step must define a shell script"
    return run_script


def _executable(path: Path, content: str) -> None:
    """Write an executable command stub."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _archive(name: str, member: str, banner: str) -> bytes:
    """Build a tar archive holding one stub executable that prints ``banner``."""
    body = f"#!/bin/sh\necho '{banner}'\n".encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(body)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


class _Sandbox(typ.NamedTuple):
    """Describe one isolated run of the install script."""

    root: Path
    curl_log: Path
    github_path: Path
    uv_log: Path

    def run(self, script: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run ``script`` with the stubs first on PATH."""
        base = {
            "HOME": str(self.root / "home"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "GITHUB_PATH": str(self.github_path),
            "PATH": f"{self.root / 'stubs'}:{os.environ['PATH']}",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
        }
        base.update(env)
        return subprocess.run(  # noqa: S603,TID251 - exercise the Bash fragment.
            ["bash", "-c", script],  # noqa: S607 - bash from PATH.
            capture_output=True,
            text=True,
            env=base,
            check=False,
        )


def _sandbox(tmp_path: Path, *, pin_fixtures: bool) -> tuple[_Sandbox, str]:
    """Create stubs and return the sandbox with the script to run in it.

    With ``pin_fixtures`` the script's Linux/X64 digests are replaced by the
    digests of the fixture archives, so the success path can be exercised
    without the network. Without it the fixtures fail the real pins.
    """
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (tmp_path / "home").mkdir()
    uv_bin = tmp_path / "uvbin"
    uv_bin.mkdir()
    # The stub installs yamllint only for the exact pinned command, so a changed
    # or missing pin leaves no `yamllint` behind and the action must fail.
    uv_log = tmp_path / "uv.log"
    _executable(
        stubs / "uv",
        "#!/bin/sh\n"
        f'echo "$*" >> "{uv_log}"\n'
        'if [ "$*" = "tool dir --bin" ]; then echo "' + str(uv_bin) + '"; exit 0; fi\n'
        'if [ "$*" = "tool install yamllint==1.38.0" ]; then\n'
        f'  printf \'#!/bin/sh\\necho "yamllint 1.38.0"\\n\' > "{uv_bin}/yamllint"\n'
        f'  chmod +x "{uv_bin}/yamllint"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    curl_log = tmp_path / "curl.log"
    _executable(
        stubs / "curl",
        "#!/bin/sh\n"
        'out=""; url=""\n'
        'while [ $# -gt 0 ]; do case "$1" in --output) out="$2"; shift;; '
        'http*) url="$1";; esac; shift; done\n'
        f'echo "$url" >> "{curl_log}"\n'
        f'cp "{fixtures}/$(basename "$url")" "$out"\n',
    )
    archives = {
        "actionlint_1.7.12_linux_amd64.tar.gz": _archive(
            "actionlint", "actionlint", "1.7.12"
        ),
        "shellcheck-v0.11.0.linux.x86_64.tar.xz": _archive(
            "shellcheck", "shellcheck-v0.11.0/shellcheck", "version: 0.11.0"
        ),
    }
    script = _script()
    for name, data in archives.items():
        (fixtures / name).write_bytes(data)
        tool = name.split("_")[0].split("-")[0]
        if pin_fixtures:
            script = script.replace(
                _LINUX_X64_DIGESTS[tool], hashlib.sha256(data).hexdigest()
            )
    return _Sandbox(tmp_path, curl_log, tmp_path / "github_path", uv_log), script


def test_action_declares_no_inputs_and_one_bash_step() -> None:
    """The action is pinned inside, so callers have nothing to override."""
    action = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert action["inputs"] == {}, "versions are pinned in the action, not passed"
    assert action["runs"]["using"] == "composite"
    assert action["runs"]["steps"][0]["shell"] == "bash"


def test_every_platform_digest_is_a_full_sha256() -> None:
    """Each configured release carries a 64-hex digest, and none is a placeholder."""
    digests = re.findall(r'_sha256="([^"]*)"', _script())
    assert len(digests) == 8, "two tools on four platforms"
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests)
    assert len(set(digests)) == len(digests), "no two archives share a digest"


def test_tool_versions_are_exact_pins() -> None:
    """No floating version, no latest lookup, and no uv directory override."""
    script = _script()
    assert re.search(r'^\s*yamllint_version="\d+\.\d+\.\d+"$', script, re.MULTILINE)
    assert 'uv tool install "yamllint==${yamllint_version}"' in script
    assert "latest" not in script
    assert "UV_TOOL_DIR" not in script
    assert "UV_CACHE_DIR" not in script


@posix_only
def test_cold_install_verifies_installs_and_exports_path(tmp_path: Path) -> None:
    """A verified download installs both binaries and exports both directories."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=True)
    result = sandbox.run(script)
    assert result.returncode == 0, result.stderr
    bin_dir = tmp_path / "cache" / "yaml-lint" / "1.7.12-0.11.0" / "bin"
    assert (bin_dir / "actionlint").is_file()
    assert (bin_dir / "shellcheck").is_file()
    exported = sandbox.github_path.read_text(encoding="utf-8").splitlines()
    # The runner prepends each line, so the verified directory goes last to win.
    assert exported == [str(tmp_path / "uvbin"), str(bin_dir)]
    assert len(sandbox.curl_log.read_text(encoding="utf-8").splitlines()) == 2
    assert sandbox.uv_log.read_text(encoding="utf-8").splitlines() == [
        "tool install yamllint==1.38.0",
        "tool dir --bin",
    ]
    assert "metric install-yaml-lint.actionlint=install" in result.stdout
    assert "metric install-yaml-lint.shellcheck=install" in result.stdout
    assert "metric install-yaml-lint.result=ok" in result.stdout


@posix_only
def test_warm_install_does_not_download_again(tmp_path: Path) -> None:
    """A second run finds both binaries at the pinned versions and fetches nothing."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=True)
    assert sandbox.run(script).returncode == 0
    sandbox.curl_log.unlink()
    result = sandbox.run(script)
    assert result.returncode == 0, result.stderr
    assert not sandbox.curl_log.exists(), "warm cache should not call curl"
    assert "metric install-yaml-lint.actionlint=cache-hit" in result.stdout
    assert "metric install-yaml-lint.shellcheck=cache-hit" in result.stdout


@posix_only
def test_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    """An archive that does not match the pin is refused and nothing is installed."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=False)
    result = sandbox.run(script)
    assert result.returncode == 1
    assert "checksum mismatch for actionlint_1.7.12_linux_amd64.tar.gz" in result.stderr
    assert "metric install-yaml-lint.result=digest-mismatch" in result.stdout
    bin_dir = tmp_path / "cache" / "yaml-lint" / "1.7.12-0.11.0" / "bin"
    assert not (bin_dir / "actionlint").exists()


@posix_only
def test_unsupported_platform_fails_closed(tmp_path: Path) -> None:
    """Windows has no configured release, so the action refuses rather than guesses."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=True)
    result = sandbox.run(script, RUNNER_OS="Windows")
    assert result.returncode == 1
    assert "no verified release is configured for Windows/X64" in result.stderr
    assert "metric install-yaml-lint.result=unsupported-platform" in result.stdout


@posix_only
def test_missing_uv_fails_before_downloading(tmp_path: Path) -> None:
    """The action names the missing prerequisite and touches nothing."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=True)
    (tmp_path / "stubs" / "uv").unlink()
    result = sandbox.run(script, PATH=f"{tmp_path / 'stubs'}:/usr/bin:/bin")
    assert result.returncode == 1
    assert "uv is required on PATH" in result.stderr
    assert "metric install-yaml-lint.result=missing-prerequisite" in result.stdout
    assert not sandbox.curl_log.exists()


@posix_only
def test_a_changed_pin_leaves_no_yamllint_and_fails(tmp_path: Path) -> None:
    """The action asks uv for the exact pin, so any other spec installs nothing."""
    sandbox, script = _sandbox(tmp_path, pin_fixtures=True)
    script = script.replace('yamllint_version="1.38.0"', 'yamllint_version="1.38.1"')

    result = sandbox.run(script)

    assert result.returncode == 1
    assert "uv could not install yamllint==1.38.1" in result.stderr
    assert "metric install-yaml-lint.result=uv-install-failed" in result.stdout
