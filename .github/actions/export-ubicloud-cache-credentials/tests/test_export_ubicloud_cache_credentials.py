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
import os
import shutil
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

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
    info: list[str]
    order: list[str]
    failure: str | None

    def result_metric(self) -> str | None:
        """Return the bounded outcome the script reported, if any."""
        prefix = "metric ubicloud-cache-credentials.result="
        reported = [line for line in self.info if line.startswith(prefix)]
        assert len(reported) <= 1, f"more than one result metric: {reported}"
        return reported[0].removeprefix(prefix) if reported else None


def _run_script(
    *, cache_url: str | None = PROXY_URL, runtime_token: str | None = RUNTIME_TOKEN
) -> CoreCalls:
    """Execute the shipped script under Node with a stubbed `core`.

    The runner variables are supplied through the subprocess environment
    rather than spliced into the script, so the script reads them exactly as
    it would on a runner and nothing test-side can alter its text.
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node not found on PATH")

    harness = f"""
      const calls = {{
        exported: {{}}, secrets: [], notices: [], info: [], order: [],
        failure: null,
      }};
      const core = {{
        exportVariable: (name, value) => {{
          calls.order.push('exportVariable');
          calls.exported[name] = value;
        }},
        setSecret: (value) => {{
          calls.order.push('setSecret');
          calls.secrets.push(value);
        }},
        notice: (message) => {{
          calls.order.push('notice');
          calls.notices.push(message);
        }},
        info: (message) => {{
          calls.order.push('info');
          calls.info.push(message);
        }},
        setFailed: (message) => {{
          calls.order.push('setFailed');
          calls.failure = message;
        }},
      }};
      (() => {{
      {_script()}
      }})();
      process.stdout.write(JSON.stringify(calls));
    """
    # Start from the host environment with every ACTIONS_* name removed, then
    # set only the variables under test, so an ambient value cannot change the
    # result. Stripping to PATH alone is not an option: on Windows, Node needs
    # SystemRoot and friends to seed its CSPRNG and aborts without them.
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("ACTIONS_")
    }
    if cache_url is not None:
        environment["ACTIONS_CACHE_URL"] = cache_url
    if runtime_token is not None:
        environment["ACTIONS_RUNTIME_TOKEN"] = runtime_token

    completed = subprocess.run(  # noqa: S603,TID251 - exercise the shipped script.
        [node, "--input-type=module", "-e", harness],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return CoreCalls(**json.loads(completed.stdout))


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
        """Each variable must be named in an export call.

        A shape check only; that the values are right is asserted by running
        the script in ``TestBehaviour``.
        """
        assert f"'{name}'," in _script() or f"'{name}'" in _script()

    def test_fails_closed_on_a_public_cache_host(self) -> None:
        """The manifest must carry the private-network guard, not just docs."""
        script = _script()

        assert "isPrivateHost" in script
        assert "core.setFailed" in script


class TestBehaviour:
    """Run the shipped script and check what it does."""

    @pytest.mark.parametrize(
        ("cache_url", "runtime_token", "expected_host", "secret_path"),
        [
            (PROXY_URL, RUNTIME_TOKEN, "10.1.2.3:51123", "e3b0c44298fc1c14"),
            (
                "http://192.168.40.9:8080/9f86d081884c7d65/",
                "second-runtime-token",
                "192.168.40.9:8080",
                "9f86d081884c7d65",
            ),
        ],
    )
    def test_exports_and_masks_exactly_what_it_was_given(
        self,
        cache_url: str,
        runtime_token: str,
        expected_host: str,
        secret_path: str,
    ) -> None:
        """Both credentials pass through unchanged, masked, and unlogged.

        Two distinct pairs, so a hard-coded value or a swapped variable would
        show rather than coincide with the fixture.
        """
        calls = _run_script(cache_url=cache_url, runtime_token=runtime_token)

        assert calls.failure is None
        assert calls.exported["ACTIONS_CACHE_URL"] == cache_url
        assert calls.exported["ACTIONS_RUNTIME_TOKEN"] == runtime_token
        assert calls.exported["ACTIONS_CACHE_SERVICE_V2"] == ""
        assert set(calls.secrets) == {cache_url, runtime_token}

        assert len(calls.notices) == 1
        notice = calls.notices[0]
        assert expected_host in notice
        assert secret_path not in notice
        assert runtime_token not in notice
        assert cache_url not in notice

    def test_masks_both_credentials_before_it_logs_anything(self) -> None:
        """Every secret must be registered before the first line of output.

        The runner redacts only what it already knows, so the order is the
        protection rather than the calls. Checking the first registration is
        not enough: a second one after the notice would leave that credential
        unredacted in the line already written.
        """
        calls = _run_script()

        registrations = [
            index for index, name in enumerate(calls.order) if name == "setSecret"
        ]
        assert len(registrations) == 2, "both the token and the URL must be masked"

        outputs = [
            index
            for index, name in enumerate(calls.order)
            if name in {"notice", "info"}
        ]
        assert outputs, "the script produced no output at all"
        assert max(registrations) < min(outputs)

    def test_reports_the_exported_outcome(self) -> None:
        """The happy path reports its bounded outcome."""
        assert _run_script().result_metric() == "exported"

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


#: Every outcome the action may report, and nothing else. Widening this in the
#: script without widening it here breaks a scraper aggregating the series.
RESULT_VOCABULARY = frozenset(
    {
        "exported",
        "missing-cache-url",
        "missing-runtime-token",
        "invalid-url",
        "public-host",
    }
)


class TestOutcomeMetric:
    """Hold the bounded outcome the action reports on every terminal path."""

    @pytest.mark.parametrize(
        ("cache_url", "runtime_token", "expected"),
        [
            (PROXY_URL, RUNTIME_TOKEN, "exported"),
            (None, RUNTIME_TOKEN, "missing-cache-url"),
            (PROXY_URL, None, "missing-runtime-token"),
            ("not-a-url", RUNTIME_TOKEN, "invalid-url"),
            ("https://example.com/token/", RUNTIME_TOKEN, "public-host"),
        ],
    )
    def test_every_terminal_path_reports_its_outcome(
        self, cache_url: str | None, runtime_token: str | None, expected: str
    ) -> None:
        """No path may exit without saying which one it took."""
        calls = _run_script(cache_url=cache_url, runtime_token=runtime_token)

        assert calls.result_metric() == expected
        assert expected in RESULT_VOCABULARY

    def test_the_metric_carries_no_identifiers(self) -> None:
        """A metric naming a host or token would defeat the masking."""
        calls = _run_script()
        reported = [line for line in calls.info if "metric " in line]

        assert reported
        for line in reported:
            assert PROXY_URL not in line
            assert RUNTIME_TOKEN not in line
            assert "10.1.2.3" not in line


def _is_private_ipv4(octets: tuple[int, int, int, int]) -> bool:
    """Return whether *octets* names an RFC 1918 or loopback address.

    An oracle written from the ranges rather than from the implementation, so
    agreeing with it means something.
    """
    first, second, *_ = octets
    if first in {10, 127}:
        return True
    if first == 192 and second == 168:
        return True
    return first == 172 and 16 <= second <= 31


OCTETS = st.tuples(*(st.integers(min_value=0, max_value=255) for _ in range(4)))


class TestPrivateHostProperty:
    """Check the host classification against an independent oracle."""

    @given(octets=OCTETS)
    @settings(max_examples=120, derandomize=True, deadline=None)
    def test_ipv4_classification_matches_the_ranges(
        self, octets: tuple[int, int, int, int]
    ) -> None:
        """Accept exactly the private IPv4 space, across the whole address."""
        address = ".".join(str(octet) for octet in octets)
        calls = _run_script(cache_url=f"http://{address}:51123/token/")

        assert (calls.failure is None) is _is_private_ipv4(octets)

    @given(leading=st.integers(min_value=0, max_value=0xFFFF))
    @settings(max_examples=60, derandomize=True, deadline=None)
    def test_ipv6_classification_covers_the_unique_local_block(
        self, leading: int
    ) -> None:
        """Accept exactly `fc00::/7`, whose first byte is `fc` or `fd`.

        A leading hextet of zero is the exception, and not a special case in
        the action: the URL parser compresses `0000::1` to `::1`, so what the
        host check sees is loopback, which is accepted on its own terms.
        """
        calls = _run_script(cache_url=f"http://[{leading:04x}::1]:51123/token/")

        expected_private = (leading >> 8) in {0xFC, 0xFD} or leading == 0
        assert (calls.failure is None) is expected_private

    @given(
        label=st.text(
            st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-"),
            min_size=1,
            max_size=8,
        ).filter(lambda value: not value.startswith("-") and not value.endswith("-"))
    )
    @settings(max_examples=40, derandomize=True, deadline=None)
    def test_a_private_prefix_on_a_name_is_never_private(self, label: str) -> None:
        """A name beginning with a private octet must never be accepted.

        This is the finding that prompted the parser: a prefix match would
        hand `10.<anything>.example` the runtime token.
        """
        calls = _run_script(cache_url=f"http://10.{label}.example/token/")

        assert calls.failure is not None
        assert calls.exported == {}
