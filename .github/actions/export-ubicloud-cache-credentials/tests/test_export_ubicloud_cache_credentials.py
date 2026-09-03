"""Tests for the Ubicloud cache credential export action.

`ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` reach action steps only, never
`run:` steps, so the action republishes them through `GITHUB_ENV`. Two things
have to hold: the manifest must keep using a pinned JavaScript action to read
them, and the script must refuse to run anywhere the values do not describe an
Ubicloud proxy.

The behavioural tests execute the script the manifest ships, under Node, with a
stub standing in for `@actions/core`, so what is measured is what runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION_DIR = Path(__file__).resolve().parents[1]
ACTION_PATH = ACTION_DIR / "action.yml"

#: The pinned JavaScript action that reads the runner-only variables.
GITHUB_SCRIPT_REFERENCE = (
    "actions/github-script@d746ffe35508b1917358783b479e04febd2b8f71"
)

#: Every variable the action must publish, mapped to where its value comes
#: from. The empty string for the v2 switch is deliberate: sccache selects the
#: v2 cache service when it is set, and Ubicloud's proxy serves v1.
EXPORTED_VARIABLES = {
    "ACTIONS_CACHE_URL": "ACTIONS_CACHE_URL",
    "ACTIONS_RUNTIME_TOKEN": "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_CACHE_SERVICE_V2": None,
}

PROXY_URL = "http://10.1.2.3:51123/e3b0c44298fc1c14/"
RUNTIME_TOKEN = "runtime-token-value"  # noqa: S105 - test fixture, not a secret


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed action manifest."""
    loaded = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _script_step() -> dict[str, typ.Any]:
    """Return the single step that runs the export script."""
    steps = _manifest()["runs"]["steps"]
    assert len(steps) == 1, "the action should run exactly one step"
    return steps[0]


def _script() -> str:
    """Return the JavaScript the manifest ships."""
    script = _script_step()["with"]["script"]
    assert isinstance(script, str)
    return script


class CoreCalls(typ.NamedTuple):
    """What the script asked the Actions toolkit to do."""

    exported: dict[str, str]
    secrets: list[str]
    notices: list[str]
    failure: str | None


def _run_script(
    *, cache_url: str | None = PROXY_URL, runtime_token: str | None = RUNTIME_TOKEN
) -> CoreCalls:
    """Execute the shipped script under Node with a stubbed `core`."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node not found on PATH")

    environment_lines = [
        f"process.env.{name} = {json.dumps(value)};"
        for name, value in (
            ("ACTIONS_CACHE_URL", cache_url),
            ("ACTIONS_RUNTIME_TOKEN", runtime_token),
        )
        if value is not None
    ]
    harness = f"""
      const calls = {{ exported: {{}}, secrets: [], notices: [], failure: null }};
      const core = {{
        exportVariable: (name, value) => {{ calls.exported[name] = value; }},
        setSecret: (value) => calls.secrets.push(value),
        notice: (message) => calls.notices.push(message),
        setFailed: (message) => {{ calls.failure = message; }},
      }};
      {chr(10).join(environment_lines)}
      (() => {{
      {_script()}
      }})();
      process.stdout.write(JSON.stringify(calls));
    """
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the shipped script.
        [node, "--input-type=module", "-e", harness],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    parsed = json.loads(completed.stdout)
    return CoreCalls(**parsed)


class TestManifest:
    """Hold the shape a caller and the runner both depend on."""

    def test_reads_the_values_through_a_pinned_javascript_action(self) -> None:
        """Only an action step sees the runner-only cache variables.

        A `run:` step cannot read them at all, so replacing this with a shell
        step would silently export nothing.
        """
        assert _script_step()["uses"] == GITHUB_SCRIPT_REFERENCE

    @pytest.mark.parametrize("name", sorted(EXPORTED_VARIABLES))
    def test_publishes_every_variable(self, name: str) -> None:
        """Each variable must be exported by the shipped script."""
        assert f"core.exportVariable('{name}'" in _script()

    def test_fails_closed_on_a_public_cache_host(self) -> None:
        """The manifest must carry the private-network guard, not just docs."""
        script = _script()

        assert "isPrivateHost" in script
        assert "core.setFailed" in script


class TestBehaviour:
    """Run the shipped script and check what it does."""

    def test_exports_the_proxy_credentials(self) -> None:
        """The happy path publishes all three variables."""
        calls = _run_script()

        assert calls.failure is None
        assert calls.exported["ACTIONS_CACHE_URL"] == PROXY_URL
        assert calls.exported["ACTIONS_RUNTIME_TOKEN"] == RUNTIME_TOKEN
        assert calls.exported["ACTIONS_CACHE_SERVICE_V2"] == ""

    def test_masks_the_token_and_the_proxy_url(self) -> None:
        """The URL path is a bearer-like secret, so both are masked."""
        calls = _run_script()

        assert RUNTIME_TOKEN in calls.secrets
        assert PROXY_URL in calls.secrets

    def test_notice_names_the_host_but_never_the_path(self) -> None:
        """The path segment is the secret, so it must not reach the log."""
        calls = _run_script()

        assert len(calls.notices) == 1
        notice = calls.notices[0]
        assert "10.1.2.3:51123" in notice
        assert "e3b0c44298fc1c14" not in notice
        assert RUNTIME_TOKEN not in notice

    @pytest.mark.parametrize(
        ("host", "expected_private"),
        [
            ("http://10.1.2.3:51123/token/", True),
            ("http://172.16.0.9:51123/token/", True),
            ("http://172.31.255.1:51123/token/", True),
            ("http://192.168.4.5:51123/token/", True),
            ("http://127.0.0.1:51123/token/", True),
            ("http://localhost:51123/token/", True),
            ("http://[fd00::1]:51123/token/", True),
            ("https://acghubeus1.actions.githubusercontent.com/abc/", False),
            ("http://172.15.0.1:51123/token/", False),
            ("http://172.32.0.1:51123/token/", False),
            ("http://8.8.8.8:51123/token/", False),
            # A DNS name that merely begins with a private-range octet. A
            # prefix match would classify these as private and hand the
            # runtime token to whoever controls them.
            ("http://10.attacker.example/token/", False),
            ("http://127.0.0.1.attacker.example/token/", False),
            ("http://192.168.attacker.example/token/", False),
            ("http://172.16.attacker.example/token/", False),
            # A name that may resolve to a private address is still refused:
            # resolution is not ours to trust, and the proxy URL is a literal.
            ("http://proxy.internal:51123/token/", False),
            # The URL parser normalizes legacy IPv4 forms before the host is
            # ever examined: 2130706433 becomes 127.0.0.1 and 10.1.2 becomes
            # 10.1.0.2. Those are the addresses the caller really named, so
            # accepting them is right.
            ("http://2130706433:51123/token/", True),
            ("http://10.1.2:51123/token/", True),
            # IPv6 outside the unique-local range.
            ("http://[2001:db8::1]:51123/token/", False),
            ("http://[f00d::1]:51123/token/", False),
            ("http://[fc00::1]:51123/token/", True),
            ("http://[fd12:3456::1]:51123/token/", True),
        ],
    )
    def test_accepts_only_private_network_proxies(
        self, host: str, *, expected_private: bool
    ) -> None:
        """A public endpoint is not an Ubicloud proxy, so refuse it.

        Exporting a GitHub-hosted cache endpoint under this action's name would
        point sccache at the wrong service while claiming otherwise. A host is
        private only when it is a complete address literal inside a private
        range: a name beginning with a private octet, such as
        ``10.attacker.example``, would otherwise be handed the runtime token.
        """
        calls = _run_script(cache_url=host)

        assert (calls.failure is None) is expected_private
        if not expected_private:
            assert "private network" in calls.failure
            assert calls.exported == {}

    def test_fails_when_the_cache_url_is_absent(self) -> None:
        """A missing URL means the caller is not on an Ubicloud runner."""
        calls = _run_script(cache_url=None)

        assert calls.failure is not None
        assert "ACTIONS_CACHE_URL is not set" in calls.failure
        assert calls.exported == {}

    def test_fails_when_the_runtime_token_is_absent(self) -> None:
        """Without the token the proxy cannot be authenticated."""
        calls = _run_script(runtime_token=None)

        assert calls.failure is not None
        assert "ACTIONS_RUNTIME_TOKEN is not set" in calls.failure
        assert calls.exported == {}

    @pytest.mark.parametrize(
        "cache_url",
        [
            "not-a-url",
            "",
            # Five octets, and an octet above 255: the URL parser rejects both
            # outright, so they never reach the private-network check.
            "http://10.1.2.3.4:51123/token/",
            "http://10.1.2.999:51123/token/",
        ],
    )
    def test_fails_on_a_malformed_url(self, cache_url: str) -> None:
        """A value that is not a URL cannot be checked, so it is refused."""
        calls = _run_script(cache_url=cache_url)

        assert calls.failure is not None
        assert calls.exported == {}
