"""Tests for the sccache server `setup-rust` starts.

sccache reads its cache configuration once, when the server starts, and never
rebinds it. Before this step existed the server started as a side effect of the
first client command, which was the `--zero-stats` in the wrapper export, and
nothing named that as the moment the backend was chosen. A reader looking for
where the cache is bound found no such step.

Starting it explicitly, last of the sccache steps, gives that moment a name, an
outcome and a position the manifest can hold: after the backend selection,
after the cache-service restore, and after whatever the caller exported before
this action ran.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import typing as typ
from pathlib import Path

import pytest
import yaml
from setup_rust_test_helpers import ACTION_PATH, get_step, requires_bash

SERVER_STEP = "Start the sccache server"
BACKEND_STEP = "Select the sccache backend"
RESTORE_STEP = "Restore the caller's cache service selection"
WRAPPER_STEP = "Export sccache as the rustc wrapper"

#: Every outcome the start may report, and nothing else.
SERVER_OUTCOMES = frozenset(
    {
        "started",
        "started-stats-not-zeroed",
        "start-failed",
        "caller-set",
        "missing-sccache-path",
    }
)


def _steps() -> list[dict[str, typ.Any]]:
    """Return the composite action's step definitions."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["runs"]["steps"]


def _server_script() -> str:
    """Return the Bash fragment the start step declares."""
    script = get_step(SERVER_STEP).get("run")
    assert isinstance(script, str), "the start step must be a shell fragment"
    return script


@pytest.fixture
def fake_sccache(tmp_path: Path) -> Path:
    """Return a stub sccache that records its arguments."""
    binary = tmp_path / "sccache"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >> "$(dirname "$0")/args.log"\n'
        'if [[ "$1" == "--start-server" ]]; then\n'
        '  exit "${FAKE_START_EXIT:-0}"\n'
        "fi\n"
        'exit "${FAKE_SCCACHE_EXIT:-0}"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


@dataclasses.dataclass(frozen=True)
class Scenario:
    """The inputs of one run of the start fragment."""

    workdir: Path
    sccache_path: str | None
    wrapper_state: str = "exported"
    start_exit: int | None = None
    other_exit: int | None = None
    caller_conf: str | None = None


def _run_server(scenario: Scenario) -> subprocess.CompletedProcess[str]:
    """Run the start fragment under a controlled environment.

    `GITHUB_ENV` and `RUNNER_TEMP` live under the scenario's `workdir`, so a
    run can be read back through `_env_file` and `_written_conf`.
    """
    workdir = scenario.workdir
    sccache_path = scenario.sccache_path
    environment = {**os.environ}
    for name in (
        "SCCACHE_CONF",
        "RUSTC_WRAPPER",
        "SCCACHE_PATH",
        "FAKE_START_EXIT",
        "FAKE_SCCACHE_EXIT",
    ):
        environment.pop(name, None)
    environment["WRAPPER_STATE"] = scenario.wrapper_state
    (workdir / "temp").mkdir(exist_ok=True)
    environment["RUNNER_TEMP"] = str(workdir / "temp")
    environment["GITHUB_ENV"] = str(workdir / "github_env")
    environment["GITHUB_OUTPUT"] = str(workdir / "github_output")
    environment["GITHUB_STEP_SUMMARY"] = str(workdir / "summary")
    if scenario.caller_conf is not None:
        environment["SCCACHE_CONF"] = scenario.caller_conf
    if sccache_path is not None:
        environment["SCCACHE_PATH"] = sccache_path
        environment["RUSTC_WRAPPER"] = sccache_path
    if scenario.start_exit is not None:
        environment["FAKE_START_EXIT"] = str(scenario.start_exit)
    if scenario.other_exit is not None:
        environment["FAKE_SCCACHE_EXIT"] = str(scenario.other_exit)
    return subprocess.run(  # noqa: S603,TID251 - exercise the action fragment.
        [requires_bash(), "-c", _server_script()],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10,
    )


def _env_file(workdir: Path) -> list[str]:
    """Return the lines the fragment appended to `GITHUB_ENV`."""
    path = workdir / "github_env"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _read(workdir: Path, name: str) -> str:
    """Return a file the fragment wrote under `workdir`, or empty."""
    path = workdir / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _written_conf(workdir: Path) -> str:
    """Return the config file `GITHUB_ENV` says later steps will read."""
    names = [
        line.removeprefix("SCCACHE_CONF=")
        for line in _env_file(workdir)
        if line.startswith("SCCACHE_CONF=")
    ]
    assert len(names) == 1, f"expected one SCCACHE_CONF export, got {names}"
    return Path(names[0]).read_text(encoding="utf-8")


def _reported(completed: subprocess.CompletedProcess[str]) -> str | None:
    """Return the bounded server outcome the fragment reported."""
    prefix = "metric setup-rust.sccache.server="
    reported = [
        line.removeprefix(prefix)
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(reported) <= 1, f"more than one server metric: {reported}"
    return reported[0] if reported else None


class TestManifest:
    """The position is the reason this step exists."""

    def test_it_reads_the_wrapper_export_outcome(self) -> None:
        """The gate is a step output, so the manifest must actually wire it."""
        step = get_step(SERVER_STEP)

        assert step["env"]["WRAPPER_STATE"] == (
            "${{ steps.rustc-wrapper.outputs.state }}"
        )
        assert get_step(WRAPPER_STEP)["id"] == "rustc-wrapper"

    def test_it_is_a_run_step(self) -> None:
        """Only a `run:` step sees what `GITHUB_ENV` carries."""
        step = get_step(SERVER_STEP)

        assert "uses" not in step
        assert isinstance(step.get("run"), str)

    @pytest.mark.parametrize("earlier", [BACKEND_STEP, RESTORE_STEP, WRAPPER_STEP])
    def test_it_follows_every_export_the_server_must_see(self, earlier: str) -> None:
        """A server started before any of these binds a stale configuration."""
        names = [step.get("name") for step in _steps()]

        assert names.index(earlier) < names.index(SERVER_STEP)

    def test_it_is_the_last_sccache_step(self) -> None:
        """Nothing after it may change what the running server already bound."""
        names = [name for name in (step.get("name") for step in _steps()) if name]
        sccache_steps = [
            name
            for name in names
            if "sccache" in name.lower() or "cache service" in name.lower()
        ]

        assert sccache_steps[-1] == SERVER_STEP

    def test_it_is_gated_on_the_same_conditions(self) -> None:
        """Starting a server on a run with no sccache would fail on a name.

        The whole predicate is compared, not its parts, so a future `||` cannot
        widen it unnoticed.
        """
        assert get_step(SERVER_STEP)["if"] == (
            "${{ inputs.use-sccache == 'true' && github.event_name != 'release' }}"
        )


class TestPinning:
    """Every sccache-action invocation names the version it installs."""

    def test_every_invocation_pins_a_version(self) -> None:
        """Left unset, the action asks the GitHub API for the latest release.

        That is a floating dependency and a network call in the critical path.
        The call timed out on #440 and failed the job with "Unable to locate
        executable file: undefined", a red check with no step log behind it.
        """
        invocations = [
            step
            for step in _steps()
            if str(step.get("uses", "")).startswith("mozilla-actions/sccache-action@")
        ]

        assert invocations, "no sccache-action step found; has it been renamed?"
        for step in invocations:
            version = step.get("with", {}).get("version")
            assert version, f"unpinned sccache-action in step {step.get('name')!r}"
            assert version.startswith("v"), version


class TestBehaviour:
    """Run the shipped fragment."""

    def test_starts_a_server(self, fake_sccache: Path) -> None:
        """The ordinary case: the binding happens here and is reported."""
        completed = _run_server(
            Scenario(workdir=fake_sccache.parent, sccache_path=str(fake_sccache))
        )

        assert completed.returncode == 0, completed.stderr
        recorded = (fake_sccache.parent / "args.log").read_text(encoding="utf-8")
        assert "--start-server" in recorded.split()
        assert _reported(completed) == "started"

    def test_stops_any_server_before_starting_one(self, fake_sccache: Path) -> None:
        """A server already running holds the backend it bound then.

        sccache never rebinds, so reusing one started before the cache-service
        restore would keep exactly the configuration this change exists to
        replace.
        """
        completed = _run_server(
            Scenario(workdir=fake_sccache.parent, sccache_path=str(fake_sccache))
        )

        assert completed.returncode == 0, completed.stderr
        recorded = (fake_sccache.parent / "args.log").read_text(encoding="utf-8")
        arguments = recorded.split()
        assert arguments.index("--stop-server") < arguments.index("--start-server")

    def test_leaves_a_caller_owned_wrapper_alone(self, fake_sccache: Path) -> None:
        """A wrapper the export step did not write means the caller owns it.

        The environment cannot say so: an inherited `RUSTC_WRAPPER` may name
        this very binary, when a caller ran `setup-rust` earlier in the job or
        nested it through `rust-build-release`. Stopping that server would
        discard the statistics of everything compiled so far, so the decision
        rests on what the export step published, not on what the value looks
        like.
        """
        completed = _run_server(
            Scenario(
                workdir=fake_sccache.parent,
                sccache_path=str(fake_sccache),
                wrapper_state="caller-set",
            )
        )

        assert completed.returncode == 0, completed.stderr
        assert not (fake_sccache.parent / "args.log").exists()
        assert _reported(completed) == "caller-set"

    def test_leaves_an_inherited_wrapper_naming_this_sccache_alone(
        self, fake_sccache: Path
    ) -> None:
        """The nested case, where the value alone would have said `ours`."""
        _run_server(
            Scenario(
                workdir=fake_sccache.parent,
                sccache_path=str(fake_sccache),
                wrapper_state="caller-set",
            )
        )

        assert not (fake_sccache.parent / "args.log").exists()

    def test_fails_when_sccache_path_is_absent(self, tmp_path: Path) -> None:
        """Continuing would leave the caller compiling uncached and unaware."""
        completed = _run_server(
            Scenario(workdir=tmp_path, sccache_path=None, wrapper_state="exported")
        )

        assert completed.returncode != 0
        assert "did not export SCCACHE_PATH" in completed.stderr
        assert _reported(completed) == "missing-sccache-path"

    def test_zeroes_the_counters_after_starting(self, fake_sccache: Path) -> None:
        """Belt and braces against a `--start-server` that adopted one.

        A server this step started has zero counters already, but a caller's
        later `--show-stats` must measure their build whichever happened.
        """
        completed = _run_server(
            Scenario(workdir=fake_sccache.parent, sccache_path=str(fake_sccache))
        )

        assert completed.returncode == 0, completed.stderr
        arguments = (fake_sccache.parent / "args.log").read_text().split()
        assert arguments.index("--start-server") < arguments.index("--zero-stats")

    def test_a_failure_to_zero_keeps_the_server(self, fake_sccache: Path) -> None:
        """Losing a baseline is a warning; losing the cache would not be."""
        completed = _run_server(
            Scenario(
                workdir=fake_sccache.parent,
                sccache_path=str(fake_sccache),
                other_exit=1,
            )
        )

        assert completed.returncode == 0, completed.stderr
        assert "could not zero sccache statistics" in completed.stdout
        assert _reported(completed) == "started-stats-not-zeroed"

    def test_a_server_that_will_not_start_warns_and_clears_the_wrapper(
        self, fake_sccache: Path
    ) -> None:
        """A cache is an optimisation, so an unreachable one must not fail a job.

        It must stay visible, so the warning is asserted, and the wrapper is
        cleared through `GITHUB_ENV` so Cargo compiles with plain rustc instead
        of failing every later `rustc` call against a dead server.
        """
        workdir = fake_sccache.parent
        completed = _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache), start_exit=1)
        )

        assert completed.returncode == 0, completed.stderr
        assert (
            "::warning title=sccache-fallback::sccache server did not start "
            "within 60 s; this job compiled without the compiler cache"
        ) in completed.stdout.splitlines()
        assert "RUSTC_WRAPPER=" in _env_file(workdir)
        assert _reported(completed) == "start-failed"

    def test_a_started_server_leaves_the_wrapper_alone(
        self, fake_sccache: Path
    ) -> None:
        """Only the failure path may clear the wrapper."""
        workdir = fake_sccache.parent
        _run_server(Scenario(workdir=workdir, sccache_path=str(fake_sccache)))

        assert "RUSTC_WRAPPER=" not in _env_file(workdir)


class TestFallbackSignals:
    """A fallback must be findable without reading a log.

    Estate-wide detectors count the annotation title, so it is a contract.
    """

    def test_the_annotation_title_is_the_contract(self, fake_sccache: Path) -> None:
        """Renaming the title would blind every detector built on it."""
        completed = _run_server(
            Scenario(
                workdir=fake_sccache.parent,
                sccache_path=str(fake_sccache),
                start_exit=1,
            )
        )

        assert "::warning title=sccache-fallback::" in completed.stdout

    def test_the_summary_line_reaches_the_run_page(self, fake_sccache: Path) -> None:
        """The summary shows on the run page, not only in the log."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache), start_exit=1)
        )

        assert (
            "sccache: FALLBACK (cache disabled for this job)"
            in _read(workdir, "summary").splitlines()
        )

    def test_the_output_says_fallback(self, fake_sccache: Path) -> None:
        """A caller workflow can act on the output."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache), start_exit=1)
        )

        assert _read(workdir, "github_output").splitlines() == ["status=fallback"]

    def test_a_started_server_raises_no_fallback_signal(
        self, fake_sccache: Path
    ) -> None:
        """A healthy start reports `started` and leaves the summary alone."""
        workdir = fake_sccache.parent
        completed = _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache))
        )

        assert "sccache-fallback" not in completed.stdout
        assert _read(workdir, "summary") == ""
        assert _read(workdir, "github_output").splitlines() == ["status=started"]

    def test_a_caller_owned_wrapper_sets_no_status(self, fake_sccache: Path) -> None:
        """When the action starts no server, the output stays empty."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                wrapper_state="caller-set",
            )
        )

        assert _read(workdir, "github_output") == ""

    def test_the_output_is_exposed_by_the_action(self) -> None:
        """The step's output must be wired to the action's own output."""
        outputs = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))["outputs"]

        assert outputs["sccache-status"]["value"] == (
            "${{ steps.sccache-server.outputs.status }}"
        )
        assert get_step(SERVER_STEP)["id"] == "sccache-server"


class TestStartupTimeout:
    """sccache's 10 s startup timeout is settable only through a config file."""

    def test_writes_a_config_with_the_longer_timeout(self, fake_sccache: Path) -> None:
        """Ubicloud's cache proxy intermittently outlasts the default."""
        workdir = fake_sccache.parent
        completed = _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache))
        )

        assert completed.returncode == 0, completed.stderr
        assert "server_startup_timeout_ms = 60000" in _written_conf(workdir)

    def test_the_server_sees_the_config_it_starts_under(
        self, fake_sccache: Path
    ) -> None:
        """Exporting to `GITHUB_ENV` alone would reach only later steps."""
        workdir = fake_sccache.parent
        binary = fake_sccache
        binary.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "--start-server" ]]; then\n'
            '  printf "%s" "${SCCACHE_CONF:-}" > "$(dirname "$0")/seen.conf"\n'
            "fi\n",
            encoding="utf-8",
        )
        _run_server(Scenario(workdir=workdir, sccache_path=str(binary)))

        seen = (workdir / "seen.conf").read_text(encoding="utf-8")
        assert seen
        assert f"SCCACHE_CONF={seen}" in _env_file(workdir)

    def test_merges_into_a_callers_config(self, fake_sccache: Path) -> None:
        """A caller's settings survive, and the new key stays top level.

        Appended after a table header the key would belong to that table, so it
        is written first.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        theirs.write_text('[cache.gha]\nversion = "x"\n', encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        merged = _written_conf(workdir)
        assert merged.startswith("server_startup_timeout_ms = 60000\n")
        assert '[cache.gha]\nversion = "x"' in merged
        assert theirs.read_text(encoding="utf-8") == '[cache.gha]\nversion = "x"\n'

    def test_a_timeout_the_caller_chose_wins(self, fake_sccache: Path) -> None:
        """An explicit value is a decision; the action does not override it."""
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        theirs.write_text("server_startup_timeout_ms = 5000\n", encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == "server_startup_timeout_ms = 5000\n"
        assert f"SCCACHE_CONF={theirs}" in _env_file(workdir)

    @pytest.mark.parametrize(
        "spelling",
        ['"server_startup_timeout_ms"', "'server_startup_timeout_ms'"],
    )
    def test_a_quoted_timeout_key_the_caller_chose_wins(
        self, fake_sccache: Path, spelling: str
    ) -> None:
        """TOML lets a key be quoted; prepending a bare twin would be a duplicate.

        A duplicate key is invalid TOML, so sccache could not start and the
        fail-open path would silently disable the cache.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        theirs.write_text(f"{spelling} = 5000\n", encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == f"{spelling} = 5000\n"

    def test_a_nested_key_is_not_the_root_timeout(self, fake_sccache: Path) -> None:
        """Only the root `server_startup_timeout_ms` sets the startup timeout.

        `[cache.multilevel]` accepts and ignores the same name, so treating it
        as a root setting would skip the required root value.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        nested = "[cache.multilevel]\nserver_startup_timeout_ms = 5000\n"
        theirs.write_text(nested, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        merged = _written_conf(workdir)
        assert merged.startswith("server_startup_timeout_ms = 60000\n")
        assert nested in merged

    @pytest.mark.parametrize("delimiter", ['"""', "'''"])
    def test_a_timeout_named_inside_a_multiline_string_is_not_the_root_key(
        self, fake_sccache: Path, delimiter: str
    ) -> None:
        """TOML treats those lines as string content, not as a root setting.

        Scanning line by line would take the string's content for the caller's
        own timeout and skip the required default.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        text = f"alpha = {delimiter}\nserver_startup_timeout_ms = 123\n{delimiter}\n"
        theirs.write_text(text, encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        assert _written_conf(workdir) == "server_startup_timeout_ms = 60000\n" + text

    def test_the_merged_config_is_private(self, fake_sccache: Path) -> None:
        """A caller's config may hold backend credentials.

        The merged copy must not be more readable than an ordinary umask would
        make it, so it is created under `umask 077`.
        """
        workdir = fake_sccache.parent
        theirs = workdir / "theirs.toml"
        theirs.write_text('[cache.s3]\nbucket = "b"\n', encoding="utf-8")
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                caller_conf=str(theirs),
            )
        )

        names = [
            line.removeprefix("SCCACHE_CONF=")
            for line in _env_file(workdir)
            if line.startswith("SCCACHE_CONF=")
        ]
        assert Path(names[0]).stat().st_mode & 0o077 == 0

    def test_a_caller_owned_wrapper_gets_no_config(self, fake_sccache: Path) -> None:
        """When the caller owns the server the action touches nothing."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                wrapper_state="caller-set",
            )
        )

        assert _env_file(workdir) == []


class TestOutcomeMetric:
    """Every terminal path reports one bounded outcome."""

    @pytest.mark.parametrize(
        ("wrapper_state", "start_exit", "expected"),
        [
            ("exported", None, "started"),
            ("caller-set", None, "caller-set"),
            ("exported", 1, "start-failed"),
        ],
    )
    def test_each_path_reports_its_own(
        self,
        fake_sccache: Path,
        wrapper_state: str,
        start_exit: int | None,
        expected: str,
    ) -> None:
        """A path that reported nothing would be invisible in the series."""
        completed = _run_server(
            Scenario(
                workdir=fake_sccache.parent,
                sccache_path=str(fake_sccache),
                wrapper_state=wrapper_state,
                start_exit=start_exit,
            )
        )

        assert _reported(completed) == expected

    def test_the_metric_names_no_path(self, fake_sccache: Path) -> None:
        """A binary path in the metric gives the series a value per runner."""
        completed = _run_server(
            Scenario(workdir=fake_sccache.parent, sccache_path=str(fake_sccache))
        )
        outcome = _reported(completed)

        assert outcome in SERVER_OUTCOMES
        assert str(fake_sccache) not in f"metric setup-rust.sccache.server={outcome}"
