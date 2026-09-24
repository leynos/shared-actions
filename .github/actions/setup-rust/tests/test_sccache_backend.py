"""Tests for the sccache backend `setup-rust` selects.

sccache stores compiler output on local disk unless `SCCACHE_GHA_ENABLED` is
set, and nothing persists that directory between jobs, so a consumer that set
only `RUSTC_WRAPPER` paid for the wrapper and got no cache across runs.

The selection is runner-aware. A private literal in `ACTIONS_CACHE_URL` is
Ubicloud's proxy, whose credentials the step publishes; any other runner with a
runtime token has GitHub's cache service; nektos/act, or a runner with no
token, gets local disk. A caller's own switch or directory still wins. The
behavioural cases run the shipped script under Node through
`sccache_backend_harness`.

Ordering is the subtle part and the manifest tests hold it. sccache binds its
backend once, when the server starts, and `GITHUB_ENV` reaches only the next
step, so the selection has to be written *before* the sccache-action steps.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml
from sccache_backend_harness import (
    ACT_RUNNER,
    BACKEND_STEP,
    BACKENDS,
    GITHUB_RUNNER,
    GITHUB_SCRIPT_REFERENCE,
    GITHUB_V2_RUNNER,
    PROXY_URL,
    RUNTIME_TOKEN,
    UBICLOUD_RUNNER,
    run_selection,
)
from setup_rust_test_helpers import ACTION_PATH, get_step

SCCACHE_STEPS = ("Run sccache (x86_64 macOS)", "Run sccache")
WRAPPER_STEP = "Export sccache as the rustc wrapper"
VALIDATE_STEP = "Validate expect-cache"

#: The three variables an Ubicloud selection publishes for sccache.
CREDENTIALS = {
    "ACTIONS_CACHE_URL": PROXY_URL,
    "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
    "ACTIONS_CACHE_SERVICE_V2": "",
}
SWITCH = {"SCCACHE_GHA_ENABLED": "true"}


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _step_names() -> list[str]:
    """Return the composite action's step names in order."""
    return [step.get("name") for step in _manifest()["runs"]["steps"]]


class TestManifest:
    """The shape the runner and every caller depend on."""

    def test_the_selection_precedes_every_sccache_step(self) -> None:
        """The server binds its backend at start, inside those steps.

        Exported afterwards the variables would be read by nobody, and the
        job would keep a local-disk cache while the log claimed otherwise.
        """
        names = _step_names()

        for sccache_step in SCCACHE_STEPS:
            assert names.index(BACKEND_STEP) < names.index(sccache_step), (
                f"{BACKEND_STEP!r} must come before {sccache_step!r}"
            )

    def test_the_wrapper_export_still_follows_them(self) -> None:
        """The wrapper needs `SCCACHE_PATH`, which those steps produce."""
        names = _step_names()

        for sccache_step in SCCACHE_STEPS:
            assert names.index(sccache_step) < names.index(WRAPPER_STEP), (
                f"{WRAPPER_STEP!r} must come after {sccache_step!r}"
            )

    def test_the_selection_is_gated_on_the_same_conditions(self) -> None:
        """Whole predicate, so a future `||` cannot widen it unnoticed."""
        assert get_step(BACKEND_STEP)["if"] == (
            "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"
        ), "the selection must run exactly when the sccache steps do"

    def test_it_reads_the_runner_through_a_pinned_javascript_action(self) -> None:
        """Only an action step sees `ACTIONS_CACHE_URL` and the runtime token.

        A `run:` step reading them would find nothing on every runner and
        select the same backend everywhere, which is the bug this replaces.
        """
        assert get_step(BACKEND_STEP)["uses"] == GITHUB_SCRIPT_REFERENCE, (
            "the selection must run in the pinned actions/github-script"
        )

    def test_the_output_is_the_selection_steps(self) -> None:
        """`cache-backend` reads the step that decides, not a copy of it."""
        step = get_step(BACKEND_STEP)
        output = _manifest()["outputs"]["cache-backend"]["value"]

        assert step["id"] == "sccache-backend", "the step id the output names"
        assert output == "${{ steps.sccache-backend.outputs.cache-backend }}", (
            f"cache-backend must read the selection step's output, not {output!r}"
        )

    def test_the_expectation_reaches_the_script_as_input(self) -> None:
        """`expect-cache` travels through `env`, never spliced into code."""
        step = get_step(BACKEND_STEP)

        assert step["env"] == {"SR_EXPECT_CACHE": "${{ inputs.expect-cache }}"}, (
            "expect-cache must reach the script through the step's env"
        )
        assert "inputs." not in step["with"]["script"], (
            "an expression spliced into the script is code injection"
        )

    def test_the_expectation_is_validated_before_the_toolchain(self) -> None:
        """A misspelt `expect-cache` fails in seconds, not after an install."""
        names = _step_names()

        assert names.index(VALIDATE_STEP) < names.index("Install cargo-binstall"), (
            f"{VALIDATE_STEP!r} must run before anything slow"
        )
        assert _manifest()["inputs"]["expect-cache"]["default"] == "any", (
            "expect-cache must default to any, so no caller changes behaviour"
        )


#: (runner, caller variables, backend, exported) for every runner case.
_CASES: typ.Final[list[typ.Any]] = [
    pytest.param(UBICLOUD_RUNNER, {}, "ubicloud", CREDENTIALS | SWITCH, id="ubicloud"),
    pytest.param(GITHUB_RUNNER, {}, "github", SWITCH, id="github-public-url"),
    pytest.param(GITHUB_V2_RUNNER, {}, "github", SWITCH, id="github-no-v1-url"),
    pytest.param(ACT_RUNNER, {}, "local", {}, id="act"),
    pytest.param({"ACTIONS_CACHE_URL": PROXY_URL}, {}, "local", {}, id="no-token"),
    pytest.param({}, {}, "local", {}, id="nothing-at-all"),
    pytest.param(
        UBICLOUD_RUNNER,
        {"ACTIONS_CACHE_SERVICE_V2": ""},
        "ubicloud",
        SWITCH,
        id="credentials-already-exported",
    ),
    pytest.param(
        UBICLOUD_RUNNER,
        {"SCCACHE_DIR": "/mnt/sccache"},
        "local",
        {},
        id="caller-directory",
    ),
    pytest.param(
        UBICLOUD_RUNNER,
        {"SCCACHE_GHA_ENABLED": "false"},
        "local",
        {},
        id="caller-switch-off",
    ),
    pytest.param(
        UBICLOUD_RUNNER, {"SCCACHE_GHA_ENABLED": ""}, "local", {}, id="caller-empty"
    ),
    pytest.param(
        UBICLOUD_RUNNER,
        {"SCCACHE_GHA_ENABLED": "ON"},
        "ubicloud",
        CREDENTIALS,
        id="caller-switch-on",
    ),
    pytest.param(
        UBICLOUD_RUNNER,
        {"SCCACHE_GHA_ENABLED": "true", "SCCACHE_DIR": "/mnt/sccache"},
        "ubicloud",
        CREDENTIALS,
        id="switch-beats-directory",
    ),
    pytest.param(
        GITHUB_RUNNER,
        {"SCCACHE_GHA_VERSION": "v2"},
        "github",
        {},
        id="caller-version",
    ),
    pytest.param(
        {
            "ACTIONS_CACHE_URL": "http://10.attacker.example/token/",
            "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
        },
        {},
        "github",
        SWITCH,
        id="private-looking-dns-name",
    ),
]


class TestSelection:
    """Run the shipped script against each kind of runner and caller."""

    @pytest.mark.parametrize(("runner", "caller", "backend", "exported"), _CASES)
    def test_each_runner_gets_its_backend(
        self,
        runner: dict[str, str],
        caller: dict[str, str],
        backend: str,
        exported: dict[str, str],
    ) -> None:
        """The runner decides which service; the caller decides whether.

        Exactly the listed variables are exported, so a case that publishes
        the runtime token where it should not, or writes a switch the caller
        already set, fails on the extra key.
        """
        calls = run_selection(runner, caller=caller)

        assert calls.failure is None, calls.failure
        assert calls.outputs == {"cache-backend": backend}, calls.outputs
        assert calls.backend_metric() == backend, calls.info
        assert calls.exported == exported, calls.exported

    def test_a_name_that_looks_private_is_never_handed_the_token(self) -> None:
        """`10.attacker.example` is a DNS name, not a private literal.

        A prefix match would classify it as Ubicloud's proxy and publish the
        runtime token for whoever controls the name.
        """
        calls = run_selection(
            {
                "ACTIONS_CACHE_URL": "http://10.attacker.example/token/",
                "ACTIONS_RUNTIME_TOKEN": RUNTIME_TOKEN,
            }
        )

        assert "ACTIONS_RUNTIME_TOKEN" not in calls.exported, calls.exported
        assert RUNTIME_TOKEN not in calls.exported.values(), calls.exported

    def test_masks_both_credentials_before_it_writes_anything(self) -> None:
        """The runner redacts only what it already knows, so order protects.

        Both secrets are registered before the first export, output, info
        line or notice, and the URL is one of them: its path segment is
        bearer-like.
        """
        calls = run_selection(UBICLOUD_RUNNER)
        registrations = [
            index for index, name in enumerate(calls.order) if name == "setSecret"
        ]
        writes = [
            index
            for index, name in enumerate(calls.order)
            if name in {"exportVariable", "setOutput", "info", "notice"}
        ]

        assert set(calls.secrets) == {PROXY_URL, RUNTIME_TOKEN}, calls.secrets
        assert max(registrations) < min(writes), calls.order

    @pytest.mark.parametrize("runner", [UBICLOUD_RUNNER, GITHUB_RUNNER, ACT_RUNNER])
    def test_nothing_logged_names_a_credential(self, runner: dict[str, str]) -> None:
        """The metric and the notice carry a closed value, never the URL."""
        calls = run_selection(runner)
        logged = [*calls.info, *calls.notices]

        for line in logged:
            assert PROXY_URL not in line, line
            assert RUNTIME_TOKEN not in line, line
            assert "10.1.2.3" not in line, line
        assert calls.backend_metric() in BACKENDS, calls.info


class TestExpectCache:
    """A job that requires a backend fails loudly without it."""

    @pytest.mark.parametrize(
        ("runner", "expect"),
        [
            pytest.param(GITHUB_RUNNER, "ubicloud", id="ubicloud-on-github"),
            pytest.param(GITHUB_V2_RUNNER, "ubicloud", id="ubicloud-no-proxy"),
            pytest.param({}, "ubicloud", id="ubicloud-no-service"),
            pytest.param(UBICLOUD_RUNNER, "github", id="github-on-ubicloud"),
            pytest.param(ACT_RUNNER, "github", id="github-under-act"),
        ],
    )
    def test_a_missing_backend_fails_and_exports_nothing(
        self, runner: dict[str, str], expect: str
    ) -> None:
        """Silent fallback on a Ubicloud-only job is worse than a red build."""
        calls = run_selection(runner, expect=expect)

        assert calls.failure is not None, "a missing backend must fail the step"
        assert f"expect-cache is {expect}" in calls.failure, calls.failure
        assert calls.exported == {}, calls.exported
        assert RUNTIME_TOKEN not in calls.failure, calls.failure

    @pytest.mark.parametrize(
        ("runner", "expect"),
        [
            pytest.param(UBICLOUD_RUNNER, "ubicloud", id="ubicloud"),
            pytest.param(GITHUB_RUNNER, "github", id="github"),
        ],
    )
    def test_the_expected_backend_passes(
        self, runner: dict[str, str], expect: str
    ) -> None:
        """Meeting the expectation changes nothing about the selection."""
        calls = run_selection(runner, expect=expect)

        assert calls.failure is None, calls.failure
        assert calls.outputs == {"cache-backend": expect}, calls.outputs

    @pytest.mark.parametrize(
        "runner", [UBICLOUD_RUNNER, GITHUB_RUNNER, GITHUB_V2_RUNNER, ACT_RUNNER, {}]
    )
    def test_any_never_fails(self, runner: dict[str, str]) -> None:
        """The default must not turn a missing service into a red build."""
        assert run_selection(runner, expect="any").failure is None

    def test_an_unknown_expectation_is_refused(self) -> None:
        """The script refuses what the validation step would, if reached."""
        calls = run_selection(UBICLOUD_RUNNER, expect="Ubicloud")

        assert calls.failure == "expect-cache must be ubicloud, github or any."
        assert calls.exported == {}, calls.exported
