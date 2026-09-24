"""Properties of `setup-rust`'s sccache backend selection.

`test_sccache_backend.py` pins named cases. This module holds what must be true
of every input: the reported backend stays inside its closed set, the output
and the metric agree, and the runtime token is published only for Ubicloud's
proxy. It also holds the private-address check in agreement with the copy in
`export-ubicloud-cache-credentials`, since both actions decide the same
question and a drift between them would mean one of them hands the token to a
host the other refuses.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from sccache_backend_harness import (
    BACKENDS,
    GITHUB_CACHE_URL,
    PROXY_URL,
    RUNTIME_TOKEN,
    run_selection,
    selection_script,
)

EXPORT_ACTION = (
    Path(__file__).resolve().parents[2]
    / "export-ubicloud-cache-credentials"
    / "action.yml"
)

#: From the first helper of the private-address check to the end of the last.
_PRIVATE_HOST_CHECK: typ.Final[re.Pattern[str]] = re.compile(
    r"^ *const parseIpv4 = .*?^ *const isPrivateHost = .*?^ *};$",
    re.MULTILINE | re.DOTALL,
)

#: Values each variable may take, including absent. Few enough that Hypothesis
#: covers their combinations, varied enough to reach every branch.
_ENVIRONMENTS = st.fixed_dictionaries(
    {},
    optional={
        "ACT": st.sampled_from(["true", ""]),
        "ACTIONS_CACHE_URL": st.sampled_from(
            [PROXY_URL, GITHUB_CACHE_URL, "http://10.attacker.example/", "junk", ""]
        ),
        "ACTIONS_RUNTIME_TOKEN": st.sampled_from([RUNTIME_TOKEN, ""]),
        "ACTIONS_CACHE_SERVICE_V2": st.sampled_from(["", "on"]),
        "SCCACHE_DIR": st.sampled_from(["/mnt/sccache", ""]),
        "SCCACHE_GHA_ENABLED": st.sampled_from(["true", "false", "ON", "1", ""]),
        "SCCACHE_GHA_VERSION": st.sampled_from(["v2", ""]),
    },
)


def _private_host_check(script: str, *, where: str) -> str:
    """Return *script*'s private-address check, dedented for comparison."""
    found = _PRIVATE_HOST_CHECK.search(script)
    assert found is not None, f"{where} carries no private-address check"
    lines = found.group(0).splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return "\n".join(line[indent:] for line in lines)


def _export_script() -> str:
    """Return the JavaScript `export-ubicloud-cache-credentials` ships."""
    manifest = yaml.safe_load(EXPORT_ACTION.read_text(encoding="utf-8"))
    return manifest["runs"]["steps"][0]["with"]["script"]


def test_both_actions_share_one_private_address_check() -> None:
    """The two copies are the same code, character for character.

    Composite actions cannot share a JavaScript module without reaching
    outside their own directory, so the check is copied, and this holds the
    copies together. A fix to one that is not made to the other fails here.
    """
    ours = _private_host_check(selection_script(), where="setup-rust")
    theirs = _private_host_check(
        _export_script(), where="export-ubicloud-cache-credentials"
    )

    assert ours == theirs, "the private-address checks have drifted apart"


@pytest.mark.parametrize(
    ("cache_url", "is_proxy"),
    [
        ("http://10.1.2.3:51123/token/", True),
        ("http://172.16.0.9:51123/token/", True),
        ("http://192.168.4.5:51123/token/", True),
        ("http://[fd00::1]:51123/token/", True),
        ("http://172.32.0.1:51123/token/", False),
        ("http://10.attacker.example/token/", False),
        ("http://proxy.internal:51123/token/", False),
        ("http://[2001:db8::1]:51123/token/", False),
    ],
)
def test_the_selection_agrees_with_the_credentials_action(
    cache_url: str, *, is_proxy: bool
) -> None:
    """A host is Ubicloud's proxy to one action exactly when it is to both.

    The export action's own suite decides these hosts; here the selection
    must reach the same verdict, driven through its whole decision rather
    than the extracted helper.
    """
    calls = run_selection(
        {"ACTIONS_CACHE_URL": cache_url, "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN}
    )

    assert (calls.outputs["cache-backend"] == "ubicloud") is is_proxy, calls.outputs


@given(environment=_ENVIRONMENTS, expect=st.sampled_from(["any", "ubicloud"]))
@settings(max_examples=60, derandomize=True, deadline=None)
def test_every_selection_stays_inside_its_contract(
    environment: dict[str, str], expect: str
) -> None:
    """Whatever the runner and caller supply, the step keeps its promises.

    The backend is one of three, the output and the metric agree, the token
    is published only with Ubicloud selected, and a switch the caller set is
    never overwritten. The deadline is off because every example starts a
    Node process, whose cost is the host's, not the script's.
    """
    calls = run_selection(environment, expect=expect)
    backend = calls.outputs["cache-backend"]

    assert backend in BACKENDS, backend
    assert calls.backend_metric() == backend, calls.info
    if "ACTIONS_RUNTIME_TOKEN" in calls.exported:
        assert backend == "ubicloud", calls.exported
    if "SCCACHE_GHA_ENABLED" in environment:
        assert "SCCACHE_GHA_ENABLED" not in calls.exported, calls.exported
    if calls.failure is not None:
        assert calls.exported == {}, calls.exported
