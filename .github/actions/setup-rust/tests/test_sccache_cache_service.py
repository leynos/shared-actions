"""Tests for the cache-service selection `setup-rust` records and restores.

The last thing `mozilla-actions/sccache-action` does is write
`ACTIONS_CACHE_SERVICE_V2=on` to `GITHUB_ENV`, along with the runner's own
`ACTIONS_RESULTS_URL` and `ACTIONS_RUNTIME_TOKEN`. On a GitHub-hosted runner
that is what a caller wants: GitHub's v1 cache service is gone. On Ubicloud the
v2 flag overrides the empty value `export-ubicloud-cache-credentials`
published, the proxy serves v1, and every write goes to the wrong endpoint.
Chutoro's Ubicloud lane recorded 164 write errors out of 301 requests that way.

All three are recorded and restored, not only the one that does harm today.
They belong to the sccache-action, and a version that started exporting a
different results URL or token would break the proxy the same way and just as
quietly.

So the caller's values are read before those steps and written back after them.
Two steps, not one, because `GITHUB_ENV` reaches only the next step: a restore
in the step that starts the server would come too late for that server.

Measured on `ubicloud-standard-2` in runs 33854048777 and 33854213968: the
runner re-injects nothing into a composite action's `run:` step, so this is the
whole of the problem rather than a part of it.
"""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

RECORD_STEP = "Record the caller's cache service selection"
RESTORE_STEP = "Restore the caller's cache service selection"
BACKEND_STEP = "Select the sccache backend"
SCCACHE_STEPS = ("Run sccache (x86_64 macOS)", "Run sccache")
WRAPPER_STEP = "Export sccache as the rustc wrapper"
SERVER_STEP = "Start the sccache server"

#: Every outcome the restore may report, and nothing else.
CACHE_SERVICE_OUTCOMES = frozenset({"restored", "unchanged", "absent"})

CONDITION = "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]


def _script(step_name: str) -> str:
    """Return the Bash fragment a named step declares."""
    script = get_step(step_name).get("run")
    assert isinstance(script, str), f"{step_name} must be a shell fragment"
    return script


def _run(
    script: str,
    tmp_path: Path,
    *,
    environment: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    """Run a fragment and return the process, `GITHUB_ENV` and `GITHUB_OUTPUT`."""
    github_env = tmp_path / "github-env"
    github_output = tmp_path / "github-output"
    github_env.touch()
    github_output.touch()
    prepared = {
        **os.environ,
        "GITHUB_ENV": str(github_env),
        "GITHUB_OUTPUT": str(github_output),
    }
    for name in (
        "ACTIONS_CACHE_SERVICE_V2",
        "ACTIONS_RESULTS_URL",
        "ACTIONS_RUNTIME_TOKEN",
        "CALLER_CACHE_SERVICE_STATE",
        "CALLER_CACHE_SERVICE_VALUE",
        "CALLER_RESULTS_URL_STATE",
        "CALLER_RESULTS_URL_VALUE",
        "CALLER_RUNTIME_TOKEN_STATE",
        "CALLER_RUNTIME_TOKEN_VALUE",
    ):
        prepared.pop(name, None)
    prepared.update(environment)
    completed = subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", script],
        capture_output=True,
        check=False,
        env=prepared,
        text=True,
        timeout=10,
    )
    return (
        completed,
        github_env.read_text(encoding="utf-8"),
        github_output.read_text(encoding="utf-8"),
    )


def _reported(
    completed: subprocess.CompletedProcess[str], metric: str = "cache-service"
) -> str | None:
    """Return the bounded outcome the fragment reported for one variable."""
    prefix = f"metric setup-rust.sccache.{metric}="
    reported = [
        line.removeprefix(prefix)
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(reported) <= 1, f"more than one {metric} metric: {reported}"
    return reported[0] if reported else None


class TestOrdering:
    """Positions are the whole mechanism here, so the manifest holds them."""

    def test_the_record_precedes_every_sccache_step(self) -> None:
        """Read after them and the value read is already the overwritten one."""
        names = [step.get("name") for step in _steps()]

        for sccache_step in SCCACHE_STEPS:
            assert names.index(RECORD_STEP) < names.index(sccache_step)

    def test_the_record_follows_the_backend_selection(self) -> None:
        """Both write before the sccache steps; the order between them is fixed.

        Keeping the selection first leaves one reading of the sequence: choose
        the backend, note what the caller had, let the action overwrite it, put
        it back.
        """
        names = [step.get("name") for step in _steps()]

        assert names.index(BACKEND_STEP) < names.index(RECORD_STEP)

    def test_the_restore_follows_every_sccache_step(self) -> None:
        """Restoring before them would be undone by the very steps it fixes."""
        names = [step.get("name") for step in _steps()]

        for sccache_step in SCCACHE_STEPS:
            assert names.index(sccache_step) < names.index(RESTORE_STEP)

    def test_the_restore_precedes_the_server_start(self) -> None:
        """`GITHUB_ENV` reaches only the next step.

        A restore in the step that starts the server would reach the step after
        it, and sccache binds its backend once and never rebinds.
        """
        names = [step.get("name") for step in _steps()]

        assert names.index(RESTORE_STEP) < names.index(SERVER_STEP)

    def test_the_restore_precedes_the_wrapper_export(self) -> None:
        """Nothing between the restore and the start may invoke sccache.

        The wrapper export sits between them and must stay free of client
        commands, which is asserted where that step is tested.
        """
        names = [step.get("name") for step in _steps()]

        assert names.index(RESTORE_STEP) < names.index(WRAPPER_STEP)

    @pytest.mark.parametrize("step_name", [RECORD_STEP, RESTORE_STEP])
    def test_both_are_run_steps(self, step_name: str) -> None:
        """An action step here would be an action step's problem to have."""
        step = get_step(step_name)

        assert "uses" not in step
        assert isinstance(step.get("run"), str)

    @pytest.mark.parametrize("step_name", [RECORD_STEP, RESTORE_STEP])
    def test_both_are_gated_on_the_same_conditions(self, step_name: str) -> None:
        """Recording without restoring, or the reverse, would strand a value.

        The whole predicate is compared rather than its parts, so a future
        `||` branch cannot widen one of the pair unnoticed.
        """
        assert get_step(step_name)["if"] == CONDITION


#: The three variables the sccache steps rewrite, with the metric each
#: reports and the output key pair carrying it between the two steps.
TRACKED = (
    ("ACTIONS_CACHE_SERVICE_V2", "cache-service", "CACHE_SERVICE"),
    ("ACTIONS_RESULTS_URL", "results-url", "RESULTS_URL"),
    ("ACTIONS_RUNTIME_TOKEN", "runtime-token", "RUNTIME_TOKEN"),
)


def _restore_environment(**recorded: str) -> dict[str, str]:
    """Return a restore environment with every tracked variable absent.

    Naming only the one under test leaves the other two unset rather than
    inheriting whatever the host has, so a test asserts about one variable
    without the others writing to the same `GITHUB_ENV`.
    """
    environment = {f"CALLER_{key}_STATE": "unset" for _, _, key in TRACKED}
    environment.update(recorded)
    return environment


class TestRecord:
    """Run the shipped recording fragment."""

    @pytest.mark.parametrize(("name", "_metric", "key"), TRACKED)
    def test_records_a_caller_value(
        self, tmp_path: Path, name: str, _metric: str, key: str
    ) -> None:
        """The ordinary GitHub-hosted case, where the runner set it."""
        _completed, _env, output = _run(
            _script(RECORD_STEP), tmp_path, environment={name: "recorded-value"}
        )
        parsed = _parse_outputs(output)

        assert parsed[f"{key.lower()}_state"] == "set"
        assert parsed[f"{key.lower()}_value"] == "recorded-value"

    @pytest.mark.parametrize(("name", "_metric", "key"), TRACKED)
    def test_records_a_cleared_value(
        self, tmp_path: Path, name: str, _metric: str, key: str
    ) -> None:
        """The Ubicloud case, and the one a `name=value` line cannot carry.

        `export-ubicloud-cache-credentials` clears `ACTIONS_CACHE_SERVICE_V2`
        rather than unsetting it, so an empty value has to survive the round
        trip intact.
        """
        _completed, _env, output = _run(
            _script(RECORD_STEP), tmp_path, environment={name: ""}
        )
        parsed = _parse_outputs(output)

        assert parsed[f"{key.lower()}_state"] == "set"
        assert parsed[f"{key.lower()}_value"] == ""

    @pytest.mark.parametrize(("_name", "_metric", "key"), TRACKED)
    def test_records_the_absence_of_a_value(
        self, tmp_path: Path, _name: str, _metric: str, key: str
    ) -> None:
        """A caller who never set it must not acquire one from the restore."""
        _completed, _env, output = _run(_script(RECORD_STEP), tmp_path, environment={})
        parsed = _parse_outputs(output)

        assert parsed[f"{key.lower()}_state"] == "unset"
        assert f"{key.lower()}_value" not in parsed

    def test_masks_the_token_and_nothing_else(self, tmp_path: Path) -> None:
        """Only the credential is masked, and it is masked before it is written.

        Masking the cache-service flag would redact the word `on` from every
        later log line in the job, so the mask is per value rather than
        blanket.
        """
        completed, _env, _output = _run(
            _script(RECORD_STEP),
            tmp_path,
            environment={
                "ACTIONS_RUNTIME_TOKEN": "a-token",
                "ACTIONS_CACHE_SERVICE_V2": "on",
                "ACTIONS_RESULTS_URL": "https://example.invalid/",
            },
        )
        masked = [
            line.removeprefix("::add-mask::")
            for line in completed.stdout.splitlines()
            if line.startswith("::add-mask::")
        ]

        assert masked == ["a-token"]


class TestRestore:
    """Run the shipped restoring fragment."""

    @pytest.mark.parametrize(("name", "metric", "key"), TRACKED)
    def test_restores_a_cleared_value_the_sccache_steps_overwrote(
        self, tmp_path: Path, name: str, metric: str, key: str
    ) -> None:
        """The failure this exists to end, in one assertion.

        The caller cleared the variable, the sccache steps set it to something
        of their own, and the server would otherwise bind GitHub's service on a
        runner whose proxy serves another.
        """
        completed, written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(
                **{
                    f"CALLER_{key}_STATE": "set",
                    f"CALLER_{key}_VALUE": "",
                    name: "the-action's-value",
                }
            ),
        )

        assert completed.returncode == 0, completed.stderr
        assert f"{name}<<" in written
        assert _reported(completed, metric) == "restored"

    @pytest.mark.parametrize(("name", "metric", "key"), TRACKED)
    def test_leaves_an_unchanged_value_alone(
        self, tmp_path: Path, name: str, metric: str, key: str
    ) -> None:
        """Rewriting an identical value would add noise and change nothing."""
        completed, written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(
                **{
                    f"CALLER_{key}_STATE": "set",
                    f"CALLER_{key}_VALUE": "unchanged",
                    name: "unchanged",
                }
            ),
        )

        assert completed.returncode == 0, completed.stderr
        assert written == ""
        assert _reported(completed, metric) == "unchanged"

    @pytest.mark.parametrize(("name", "metric", "_key"), TRACKED)
    def test_writes_nothing_when_the_caller_had_no_value(
        self, tmp_path: Path, name: str, metric: str, _key: str
    ) -> None:
        """On a GitHub-hosted runner the action's own values are the right ones.

        Restoring an absence would clear them and send sccache at a service
        GitHub no longer runs.
        """
        completed, written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(**{name: "the-action's-value"}),
        )

        assert completed.returncode == 0, completed.stderr
        assert written == ""
        assert _reported(completed, metric) == "absent"

    @pytest.mark.parametrize(("name", "metric", "key"), TRACKED)
    def test_restores_a_value_the_steps_removed_entirely(
        self, tmp_path: Path, name: str, metric: str, key: str
    ) -> None:
        """Set to unset is a change too, and the guard must catch it."""
        completed, written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(
                **{f"CALLER_{key}_STATE": "set", f"CALLER_{key}_VALUE": "recorded"}
            ),
        )

        assert completed.returncode == 0, completed.stderr
        assert f"{name}<<" in written
        assert "recorded" in written
        assert _reported(completed, metric) == "restored"

    @pytest.mark.parametrize(("name", "metric", "key"), TRACKED)
    def test_the_metric_stays_inside_its_closed_set(
        self, tmp_path: Path, name: str, metric: str, key: str
    ) -> None:
        """A scraper aggregating the series needs the values bounded."""
        completed, _written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(
                **{
                    f"CALLER_{key}_STATE": "set",
                    f"CALLER_{key}_VALUE": "",
                    name: "on",
                }
            ),
        )

        assert _reported(completed, metric) in CACHE_SERVICE_OUTCOMES

    def test_the_notice_never_carries_a_value(self, tmp_path: Path) -> None:
        """One of the three is a credential, so the notices name variables."""
        completed, _written, _output = _run(
            _script(RESTORE_STEP),
            tmp_path,
            environment=_restore_environment(
                **{
                    "CALLER_RUNTIME_TOKEN_STATE": "set",
                    "CALLER_RUNTIME_TOKEN_VALUE": "the-caller-token",
                    "ACTIONS_RUNTIME_TOKEN": "the-action-token",
                }
            ),
        )

        assert "ACTIONS_RUNTIME_TOKEN" in completed.stdout
        assert "the-caller-token" not in completed.stdout
        assert "the-action-token" not in completed.stdout


class TestRoundTrip:
    """The pair only works if what one writes the other can read."""

    @pytest.mark.parametrize("value", ["on", "", "off"])
    def test_a_recorded_value_survives_the_restore(
        self, tmp_path: Path, value: str
    ) -> None:
        """Run both fragments in sequence, as the job does.

        The recorded output is parsed the way the runner parses it, so the
        delimiter form is exercised rather than assumed.
        """
        _completed, _env, output = _run(
            _script(RECORD_STEP),
            tmp_path,
            environment={"ACTIONS_CACHE_SERVICE_V2": value},
        )
        recorded = _parse_outputs(output)

        restore_root = tmp_path / "restore"
        restore_root.mkdir()
        completed, written, _output = _run(
            _script(RESTORE_STEP),
            restore_root,
            environment=_restore_environment(
                **{
                    "CALLER_CACHE_SERVICE_STATE": recorded["cache_service_state"],
                    "CALLER_CACHE_SERVICE_VALUE": recorded["cache_service_value"],
                    "ACTIONS_CACHE_SERVICE_V2": "on",
                }
            ),
        )

        assert completed.returncode == 0, completed.stderr
        if value == "on":
            assert _reported(completed) == "unchanged"
        else:
            assert "ACTIONS_CACHE_SERVICE_V2<<" in written
            assert value in written.splitlines()


def _parse_outputs(raw: str) -> dict[str, str]:
    """Parse a `GITHUB_OUTPUT` file the way the runner does.

    Only the two forms this action writes are handled: `name=value` and the
    heredoc delimiter form that carries an empty or multi-line value.
    """
    parsed: dict[str, str] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        name, _, value = lines[index].partition("=")
        if "<<" in name:
            name, _, delimiter = name.partition("<<")
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            parsed[name] = "\n".join(body)
        else:
            parsed[name] = value
        index += 1
    return parsed
