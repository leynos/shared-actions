"""Run `setup-rust`'s sccache backend selection under Node.

The selection step is a JavaScript action, because the runner hands
`ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` to action steps only. These
helpers execute the script the manifest ships, with a stub standing in for
`@actions/core`, so what the tests measure is what runs. The runner variables
travel through the subprocess environment rather than being spliced into the
script, so nothing test-side can alter its text.

Not a test module: pytest does not collect it. The cases live in
`test_sccache_backend.py` and `test_sccache_backend_properties.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import typing as typ

import pytest
from setup_rust_test_helpers import get_step

BACKEND_STEP = "Select the sccache backend"

#: The pinned JavaScript action that reads the runner-only variables. The
#: same revision as `export-ubicloud-cache-credentials`.
GITHUB_SCRIPT_REFERENCE = (
    "actions/github-script@d746ffe35508b1917358783b479e04febd2b8f71"
)

#: Every backend the step may select, and nothing else. The output, the
#: metric and `expect-cache` share it.
BACKENDS = frozenset({"ubicloud", "github", "local"})

#: Every variable the selection reads. Each run starts with all of them
#: removed, so an ambient value on the test host cannot change a result.
SELECTION_VARIABLES = (
    "ACT",
    "ACTIONS_CACHE_SERVICE_V2",
    "ACTIONS_CACHE_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "SCCACHE_DIR",
    "SCCACHE_GHA_ENABLED",
    "SCCACHE_GHA_VERSION",
    "SR_CACHE_PROVIDER",
    "SR_EXPECT_CACHE",
    "SR_SCCACHE_DIR",
)

PROXY_URL = "http://10.1.2.3:51123/e3b0c44298fc1c14/"
GITHUB_CACHE_URL = "https://acghubeus1.actions.githubusercontent.com/abc/"
RUNTIME_TOKEN = "runtime-token-value"  # noqa: S105 - test fixture, not a secret
#: Where the step is told the runner's temporary directory puts sccache.
SCCACHE_DIR = "/home/runner/work/_temp/sccache"

#: The runner's own variables on each kind of runner.
UBICLOUD_RUNNER: typ.Final[dict[str, str]] = {
    "ACTIONS_CACHE_URL": PROXY_URL,
    "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
}
GITHUB_RUNNER: typ.Final[dict[str, str]] = {
    "ACTIONS_CACHE_URL": GITHUB_CACHE_URL,
    "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
}
#: A GitHub-hosted runner on the v2 cache service may publish no v1 URL.
GITHUB_V2_RUNNER: typ.Final[dict[str, str]] = {
    "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
}
#: nektos/act sets ACT and serves its own cache on a private address.
ACT_RUNNER: typ.Final[dict[str, str]] = {
    "ACT": "true",
    "ACTIONS_CACHE_URL": "http://172.17.0.1:34567/",
    "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
}


class CoreCalls(typ.NamedTuple):
    """What the script asked the Actions toolkit to do, in order."""

    exported: dict[str, str]
    outputs: dict[str, str]
    secrets: list[str]
    notices: list[str]
    info: list[str]
    order: list[str]
    failure: str | None

    def backend_metric(self) -> str | None:
        """Return the bounded backend the script reported, if any."""
        prefix = "metric setup-rust.sccache.backend="
        reported = [line for line in self.info if line.startswith(prefix)]
        assert len(reported) <= 1, f"more than one backend metric: {reported}"  # noqa: S101
        return reported[0].removeprefix(prefix) if reported else None


def selection_script() -> str:
    """Return the JavaScript the selection step ships."""
    script = typ.cast("dict[str, object]", get_step(BACKEND_STEP)["with"])["script"]
    assert isinstance(script, str), "the selection step must carry a script"  # noqa: S101
    return script


_HARNESS = """
  const calls = {
    exported: {}, outputs: {}, secrets: [], notices: [], info: [], order: [],
    failure: null,
  };
  const record = (name) => calls.order.push(name);
  const core = {
    exportVariable: (name, value) => {
      record('exportVariable');
      calls.exported[name] = value;
    },
    setOutput: (name, value) => {
      record('setOutput');
      calls.outputs[name] = value;
    },
    setSecret: (value) => {
      record('setSecret');
      calls.secrets.push(value);
    },
    notice: (message) => {
      record('notice');
      calls.notices.push(message);
    },
    info: (message) => {
      record('info');
      calls.info.push(message);
    },
    setFailed: (message) => {
      record('setFailed');
      calls.failure = message;
    },
  };
  (() => {
  %SCRIPT%
  })();
  process.stdout.write(JSON.stringify(calls));
"""


def run_selection(
    runner: typ.Mapping[str, str],
    *,
    expect: str = "any",
    caller: typ.Mapping[str, str] | None = None,
    cache_provider: str = "github",
) -> CoreCalls:
    """Execute the shipped selection with *runner* and *caller* variables.

    *runner* stands for what the runner itself supplies, and *caller* for what
    the workflow or an earlier step exported. They are kept apart only so a
    case reads as the situation it models; both reach the script as its
    environment.
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node not found on PATH")
    # Start from the host environment with every variable the selection reads
    # removed. Stripping to PATH alone is not an option: on Windows, Node needs
    # SystemRoot and friends to seed its CSPRNG and aborts without them.
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in SELECTION_VARIABLES
    }
    environment |= {
        **runner,
        **(caller or {}),
        "SR_EXPECT_CACHE": expect,
        "SR_CACHE_PROVIDER": cache_provider,
        "SR_SCCACHE_DIR": SCCACHE_DIR,
    }
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the shipped script.
        [
            node,
            "--input-type=module",
            "-e",
            _HARNESS.replace("%SCRIPT%", selection_script()),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr  # noqa: S101
    return CoreCalls(**json.loads(completed.stdout))
