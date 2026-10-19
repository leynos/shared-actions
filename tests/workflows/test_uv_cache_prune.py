"""Refuse a restored uv cache that carries another runner's script environment.

`uv run --script` and an ephemeral `uv run --with` build an environment under
`environments-v2`, and each entry is bound to the interpreter that built it. A
GitHub Actions cache saved on one runner is restored on another whose Python
patch version may differ;
the Windows launcher then exits 103 with "did not find executable", which is
what stopped every Windows `build-release` lane on PR #412. The restore was
made by the calling workflow's own `Setup uv`, while the composite action's
`Setup uv` missed its key and inherited the same `UV_CACHE_DIR`.

Two independent controls are asserted here, because either alone leaves the
window open:

- every caching `astral-sh/setup-uv` step in a lane that runs `uv` is given
  `prune-cache: true`, so no interpreter-bound environment is ever saved, and
  the key lineage moves away from archives that already contain one;
- every direct workflow lane and composite action that runs `uv` prunes once
  after the relevant `setup-uv` and before its first invocation, so an
  environment restored by an earlier step of the calling job is removed
  before it can be used.

The second matters on its own because `prune-cache` only governs the save.
A one-time restore of an old archive, or a caller that restores the cache
itself, is otherwise untouched.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import re
import shutil
import typing as typ
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from plumbum import local

from . import _workflow_reading as reading
from .workflow_yaml import load_workflow

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
ACTIONS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "actions"
PRUNE_SCRIPT: typ.Final[Path] = (
    REPOSITORY_ROOT / ".github" / "scripts" / "prune_uv_cache.sh"
)

#: The pinned setup-uv revision every caching step uses.
SETUP_UV_PREFIX: typ.Final[str] = "astral-sh/setup-uv@"

#: The step name of the restore-time prune.
PRUNE_STEP: typ.Final[str] = "Prune restored uv cache"
_UV_COMMAND: typ.Final[re.Pattern[str]] = re.compile(r"(?<![\w./-])uvx?(?![\w.-])")

#: No lane is exempt. Measured against uv 0.12.19, `uv run --script` and an
#: ephemeral `uv run --with` both write an `environments-v2` entry, while a
#: plain `uv run` in a project uses the project `.venv` and `uv tool
#: install`/`uv tool run` write none. A lane that only does the latter still
#: shares its cache key with every lane on the runner image, so a poisoned
#: archive written there is restored elsewhere; the input costs nothing and
#: ends that. Exempting a lane would be a code change needing its own
#: evidence, not a silent omission.


def _steps(document: object) -> list[dict[str, typ.Any]]:
    """Return validated steps from a workflow job or composite action."""
    if not isinstance(document, dict):
        message = f"manifest is not a mapping: {document!r}"
        raise TypeError(message)
    steps = _manifest_steps(document)
    if not isinstance(steps, list):
        message = f"manifest has no step list: {steps!r}"
        raise TypeError(message)
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            message = f"manifest step {index} is not a mapping: {step!r}"
            raise TypeError(message)
    return typ.cast("list[dict[str, typ.Any]]", steps)


def _manifest_steps(document: dict[str, typ.Any]) -> object:
    """Return a workflow's steps or a composite action's steps."""
    if "runs" not in document:
        return document.get("steps")
    runs = document["runs"]
    if not isinstance(runs, dict):
        message = f"'runs' is not a mapping: {runs!r}"
        raise TypeError(message)
    return runs.get("steps")


def _is_true(value: object) -> bool:
    """Return whether an action input is the exact boolean `true`.

    setup-uv compares the input text in a case-insensitive way, so a YAML
    boolean and the strings `true`/`True` are the same input. An expression
    is not a boolean and is never accepted here: `prune-cache` must be a
    literal, or the contract would read a value setup-uv cannot evaluate.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _caches_by_default(step: dict[str, typ.Any]) -> bool:
    """Return whether a setup-uv step can write a cache entry.

    `enable-cache` defaults to `auto`, so a step that omits it is a writer on
    a GitHub-hosted runner and must be held to the same rule as one that
    spells `true`. The repository's steps that spell an expression name
    `github-hosted`, which means they cache whenever the expression is true,
    and a disabled step spells `false` exactly. Anything else is read as a
    writer deliberately: an unrecognised value fails closed, not open.
    """
    given = (step.get("with") or {}).get("enable-cache")
    if given is None:
        return True
    if isinstance(given, bool):
        return given
    return str(given).strip().lower() != "false"


def _is_setup_uv(step: dict[str, typ.Any]) -> bool:
    """Return whether *step* invokes setup-uv."""
    return str(step.get("uses", "")).startswith(SETUP_UV_PREFIX)


def _prunes(step: dict[str, typ.Any]) -> bool:
    """Return whether *step* invokes the shared uv cache prune script."""
    run = step.get("run")
    if not isinstance(run, str):
        return False
    return any(
        line.strip().startswith("bash ") and "prune_uv_cache.sh" in line
        for line in run.splitlines()
    )


def _runs_uv(steps: list[dict[str, typ.Any]]) -> bool:
    """Return whether any step in the list invokes `uv`."""
    return any(_is_uv_run(step) for step in steps)


def _is_uv_run(step: dict[str, typ.Any]) -> bool:
    """Return whether a step's run command invokes `uv`."""
    run = step.get("run")
    return isinstance(run, str) and _UV_COMMAND.search(run) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run --script check.py",
        "uv tool run actionlint",
        "uvx --from yamllint yamllint .",
    ],
)
def test_is_uv_run_recognizes_uv_and_uvx_commands(command: str) -> None:
    """Recognize uv and uvx tokens, including the existing tool-run form."""
    assert _is_uv_run({"run": command}), f"did not recognize uv command: {command!r}"


@pytest.mark.parametrize("command", ["notuv run check.py", "uvx-wrapper check.py"])
def test_is_uv_run_rejects_unrelated_command_names(command: str) -> None:
    """Do not treat longer command names containing uv as uv invocations."""
    assert not _is_uv_run({"run": command}), (
        f"incorrectly recognized unrelated command: {command!r}"
    )


def _action_manifest(entry: Path) -> Path | None:
    """Return a validated action manifest, or `None` for a support entry."""
    if not entry.is_dir() or entry.name == "tests":
        return None

    manifest = entry / "action.yml"
    if not manifest.is_file():
        message = f"action directory {entry} is missing action.yml"
        raise ValueError(message)
    return manifest


def _action_paths(directory: Path = ACTIONS_DIRECTORY) -> list[Path]:
    """Return every composite action manifest, sorted and validated.

    The repository's `tests` directory is a support folder rather than an
    action. Every other directory directly under `.github/actions` is an
    action and must contain its manifest.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as error:
        message = f"cannot list actions in {directory}: {error}"
        raise ValueError(message) from error

    action_paths = [
        manifest
        for entry in entries
        if (manifest := _action_manifest(entry)) is not None
    ]
    if not action_paths:
        message = f"no action manifests found in {directory}"
        raise ValueError(message)
    return action_paths


def _action_ids() -> list[str]:
    """Return the directory name of every composite action, for pytest ids."""
    return [path.parent.name for path in _action_paths()]


def _local_actions_running_uv() -> frozenset[str]:
    """Return local composite-action paths whose steps invoke `uv`."""
    return frozenset(
        f"./.github/actions/{path.parent.name}"
        for path in _action_paths()
        if _runs_uv(_steps(load_workflow(path)))
    )


def _local_actions_pruning_cache() -> frozenset[str]:
    """Return local actions with an explicit restore-time prune step."""
    return frozenset(
        f"./.github/actions/{path.parent.name}"
        for path in _action_paths()
        if _prune_indexes(_steps(load_workflow(path)))
    )


def _uses_local_uv_action(
    steps: list[dict[str, typ.Any]], local_uv_actions: frozenset[str]
) -> bool:
    """Return whether workflow steps invoke a local action that runs `uv`."""
    return any(
        isinstance(step.get("uses"), str) and step["uses"] in local_uv_actions
        for step in steps
    )


def _workflow_uv_jobs() -> list[tuple[str, str, list[dict[str, typ.Any]]]]:
    """Return workflow jobs that run `uv` directly or through a local action."""
    found: list[tuple[str, str, list[dict[str, typ.Any]]]] = []
    local_uv_actions = _local_actions_running_uv()
    for name in reading.workflow_names():
        document = reading.load_workflow(name)
        jobs = typ.cast("dict[str, typ.Any]", document)["jobs"]
        for job_id, body in jobs.items():
            steps = body.get("steps") or []
            if _runs_uv(steps) or _uses_local_uv_action(steps, local_uv_actions):
                found.append((name, job_id, steps))
    return found


def test_workflow_uv_jobs_includes_local_uv_actions() -> None:
    """Include the YAML lint lane, whose local action is its only uv caller."""
    workflow_jobs = {(name, job_id) for name, job_id, _ in _workflow_uv_jobs()}
    assert ("test-install-yaml-lint.yml", "install-yaml-lint") in workflow_jobs


def _setup_uv_indexes(steps: list[dict[str, typ.Any]]) -> list[int]:
    """Return positions of setup-uv steps."""
    return [index for index, step in enumerate(steps) if _is_setup_uv(step)]


def _is_named_prune(step: dict[str, typ.Any]) -> bool:
    """Return whether *step* is the named cache-prune command."""
    return step.get("name") == PRUNE_STEP and _prunes(step)


def _prune_indexes(steps: list[dict[str, typ.Any]]) -> list[int]:
    """Return positions of named uv cache prune steps."""
    return [index for index, step in enumerate(steps) if _is_named_prune(step)]


def _non_prune_uv_run_indexes(steps: list[dict[str, typ.Any]]) -> list[int]:
    """Return positions of uv runs other than the cache prune itself."""
    return [
        index
        for index, step in enumerate(steps)
        if _is_uv_run(step) and not _prunes(step)
    ]


def _validate_action_prune_order(
    action_id: str, steps: list[dict[str, typ.Any]]
) -> None:
    """Require pruning after local setup-uv and before the first uv run."""
    uv_run_indexes = _non_prune_uv_run_indexes(steps)
    if not uv_run_indexes:
        return

    first_uv_run = min(uv_run_indexes)
    prune_indexes = _prune_indexes(steps)
    assert prune_indexes, f"{action_id}: no {PRUNE_STEP!r} step"
    first_prune = min(prune_indexes)
    assert first_prune < first_uv_run, (
        f"{action_id}: {PRUNE_STEP!r} must precede the first `uv` run "
        f"at step {first_uv_run}, found prune at {first_prune}"
    )

    setup_indexes = _setup_uv_indexes(steps)
    if setup_indexes:
        assert max(setup_indexes) < first_prune, (
            f"{action_id}: {PRUNE_STEP!r} must follow the last setup-uv "
            f"step at {max(setup_indexes)}, found prune at {first_prune}"
        )


def _local_action_indexes(
    steps: list[dict[str, typ.Any]], actions: frozenset[str]
) -> list[int]:
    """Return positions of local actions in *actions*."""
    return [
        index
        for index, step in enumerate(steps)
        if isinstance(step.get("uses"), str) and step["uses"] in actions
    ]


def _probe_wheel_files() -> dict[str, bytes]:
    """Return a minimal local wheel that can seed uv's ephemeral environment."""
    return {
        "uv_cache_prune_probe/__init__.py": b"VALUE = 'cache-prune-probe'\n",
        "uv_cache_prune_probe-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: uv-cache-prune-probe\nVersion: 1.0\n\n"
        ),
        "uv_cache_prune_probe-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n\n"
        ),
    }


def _wheel_record(files: dict[str, bytes], record_name: str) -> bytes:
    """Return the CSV RECORD for a small pure-Python wheel."""
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest())
        writer.writerow((name, f"sha256={digest.rstrip(b'=').decode()}", len(content)))
    writer.writerow((record_name, "", ""))
    return record.getvalue().encode()


def _write_probe_wheel(path: Path) -> Path:
    """Write a local wheel without invoking a build backend or the network."""
    files = _probe_wheel_files()
    record_name = "uv_cache_prune_probe-1.0.dist-info/RECORD"
    files[record_name] = _wheel_record(files, record_name)
    with zipfile.ZipFile(path, "w") as wheel:
        for name, contents in files.items():
            wheel.writestr(name, contents)
    return path


def _run_uv(
    executable: str, cache: Path, cwd: Path, *arguments: str
) -> tuple[int, str, str]:
    """Run uv with an isolated cache and no interpreter downloads."""
    command = (
        local[executable][list(arguments)]
        .with_cwd(cwd)
        .with_env(
            UV_CACHE_DIR=str(cache),
            UV_PYTHON_DOWNLOADS="never",
            UV_NO_PROGRESS="1",
        )
    )
    return command.run(retcode=None)


def _run_prune_script(
    cache: Path,
    cwd: Path,
    summary: Path,
    path: str | None = None,
) -> tuple[int, str, str]:
    """Run the shared prune script with an isolated cache and summary file."""
    environment = {
        "UV_CACHE_DIR": str(cache),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if path is not None:
        environment["PATH"] = path
    command = local["bash"][str(PRUNE_SCRIPT)].with_cwd(cwd).with_env(**environment)
    return command.run(retcode=None)


def _run_manifest_prune_step(
    action_id: str,
    cache: Path,
    summary: Path,
    path: str | None = None,
) -> tuple[int, str, str]:
    """Run an action manifest's real prune step with isolated cache inputs."""
    manifest = ACTIONS_DIRECTORY / action_id / "action.yml"
    steps = _steps(load_workflow(manifest))
    prune_steps = [step for step in steps if _is_named_prune(step)]
    assert len(prune_steps) == 1, (
        f"{action_id}: expected one {PRUNE_STEP!r} step, found {len(prune_steps)}"
    )
    step = prune_steps[0]
    shell = step.get("shell")
    run = step.get("run")
    assert isinstance(shell, str), f"{action_id}: prune step has no shell"
    assert isinstance(run, str), f"{action_id}: prune step has no run command"
    action_path = manifest.parent.as_posix()
    run = run.replace("${{ github.action_path }}", action_path)
    assert "${{ github.action_path }}" not in run, (
        f"{action_id}: unresolved github.action_path in prune command"
    )

    environment = {
        "GITHUB_STEP_SUMMARY": str(summary),
        "UV_CACHE_DIR": str(cache),
        "UV_NO_PROGRESS": "1",
        "UV_PYTHON_DOWNLOADS": "never",
    }
    if path is not None:
        environment["PATH"] = path
    command = (
        local[shell]["--noprofile", "--norc", "-e", "-o", "pipefail", "-c", run]
        .with_cwd(summary.parent)
        .with_env(**environment)
    )
    return command.run(retcode=None)


def test_action_manifest_discovery_rejects_a_missing_directory(tmp_path: Path) -> None:
    """Fail with the path when the action directory cannot be listed."""
    missing = tmp_path / "missing-actions"
    with pytest.raises(ValueError, match="cannot list actions"):
        _action_paths(missing)


def test_action_manifest_discovery_rejects_a_missing_manifest(
    tmp_path: Path,
) -> None:
    """Fail collection when an action directory has no manifest."""
    (tmp_path / "broken-action").mkdir()
    with pytest.raises(ValueError, match=r"missing action\.yml"):
        _action_paths(tmp_path)


def test_steps_rejects_non_mapping_entries() -> None:
    """Report malformed step entries at the manifest boundary."""
    document = {"runs": {"using": "composite", "steps": [None]}}
    with pytest.raises(TypeError, match="step 0 is not a mapping"):
        _steps(document)


@pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("bash") is None,
    reason="uv and bash are required for the action boundary test",
)
def test_uv_cache_prune_removes_a_script_environment(tmp_path: Path) -> None:
    """Prune through the action step before a later uv command uses the cache."""
    executable = shutil.which("uv")
    assert executable is not None, "uv executable is required for this test"

    cache = tmp_path / "uv-cache"
    wheel = _write_probe_wheel(tmp_path / "uv_cache_prune_probe-1.0-py3-none-any.whl")
    script = tmp_path / "run_probe.py"
    script.write_text(
        "from uv_cache_prune_probe import VALUE\nprint(VALUE)\n",
        encoding="utf-8",
    )
    run = _run_uv(
        executable,
        cache,
        tmp_path,
        "run",
        "--no-project",
        "--with",
        str(wheel),
        str(script),
    )
    run_code, stdout, stderr = run
    assert run_code == 0, f"uv --with exited {run_code}, expected 0: {stderr!r}"
    assert stdout.strip() == "cache-prune-probe", (
        f"uv --with printed unexpected output: {stdout!r}"
    )

    environments = cache / "environments-v2"
    assert environments.is_dir(), f"uv --with did not create {environments}"
    entries = list(environments.iterdir())
    assert entries, f"uv --with created no entries under {environments}"

    summary = tmp_path / "success-summary.md"
    prune_code, prune_stdout, prune_stderr = _run_manifest_prune_step(
        "install-yaml-lint", cache, summary
    )
    assert prune_code == 0, (
        f"uv cache prune exited {prune_code}, expected 0: {prune_stderr!r}"
    )
    success_metric = "metric uv-cache-prune.outcome=success"
    assert success_metric in prune_stdout, (
        f"success metric missing from stdout: {prune_stdout!r}"
    )
    summary_text = summary.read_text(encoding="utf-8")
    assert success_metric in summary_text, (
        f"success metric missing from step summary: {summary_text!r}"
    )
    remaining = list(environments.iterdir()) if environments.exists() else []
    assert not remaining, f"uv cache prune left environment entries: {remaining}"

    later_run = _run_uv(
        executable,
        cache,
        tmp_path,
        "run",
        "--no-project",
        "--with",
        str(wheel),
        str(script),
    )
    later_code, later_stdout, later_stderr = later_run
    assert later_code == 0, (
        f"later uv command exited {later_code}, expected 0: {later_stderr!r}"
    )
    assert later_stdout.strip() == "cache-prune-probe", (
        f"later uv command printed unexpected output: {later_stdout!r}"
    )


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="uses a POSIX uv command stub through the action shell",
)
def test_action_prune_step_reports_failure_and_preserves_uv_status(
    tmp_path: Path,
) -> None:
    """Fail the manifest boundary with uv's status and a bounded metric."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 37\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    summary = tmp_path / "failure-summary.md"
    path = os.pathsep.join((str(fake_bin), os.environ["PATH"]))

    exit_code, stdout, stderr = _run_manifest_prune_step(
        "install-yaml-lint", tmp_path / "uv-cache", summary, path
    )

    assert exit_code == 37, (
        f"uv cache prune exited {exit_code}, expected uv status 37: {stderr!r}"
    )
    failure_metric = "metric uv-cache-prune.outcome=failure"
    assert failure_metric in stdout, f"failure metric missing from stdout: {stdout!r}"
    summary_text = summary.read_text(encoding="utf-8")
    assert failure_metric in summary_text, (
        f"failure metric missing from step summary: {summary_text!r}"
    )


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX uv command stub")
def test_prune_success_survives_an_unwritable_step_summary(tmp_path: Path) -> None:
    """A summary-write error must not fail an otherwise successful prune."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    summary = tmp_path / "summary-directory"
    summary.mkdir()
    path = os.pathsep.join((str(fake_bin), os.environ["PATH"]))

    exit_code, stdout, stderr = _run_prune_script(
        tmp_path / "uv-cache", tmp_path, summary, path
    )

    assert exit_code == 0, f"successful uv cache prune returned {exit_code}: {stderr!r}"
    success_metric = "metric uv-cache-prune.outcome=success"
    assert success_metric in stdout, f"success metric missing from stdout: {stdout!r}"
    assert "Could not write the uv cache prune success metric." in stderr, (
        f"expected a summary-write warning in stderr: {stderr!r}"
    )


class TestCachingSetupUvStepsPrune:
    """A `setup-uv` that may write a cache must never archive environments."""

    @pytest.mark.parametrize("action_id", _action_ids())
    def test_each_caching_setup_uv_prunes_before_saving(self, action_id: str) -> None:
        """Every cache-writing step declares `prune-cache: true`.

        The input is asserted as the exact string `true`: setup-uv compares
        the input text, so a YAML boolean or another truthy spelling would
        not enable pruning at all.
        """
        manifest = REPOSITORY_ROOT / ".github" / "actions" / action_id / "action.yml"
        steps = _steps(load_workflow(manifest))
        caching = [
            step for step in steps if _is_setup_uv(step) and _caches_by_default(step)
        ]

        for step in caching:
            given = (step.get("with") or {}).get("prune-cache")
            assert _is_true(given), (
                f"{action_id}: {step.get('name')!r} may save a uv cache without "
                f"prune-cache: true, got {given!r}"
            )

    @pytest.mark.parametrize(("name", "job_id", "steps"), _workflow_uv_jobs())
    def test_each_workflow_lane_prunes_before_saving(
        self, name: str, job_id: str, steps: list[dict[str, typ.Any]]
    ) -> None:
        """Every cache-writing step in a `uv` lane declares the same input."""
        caching = [
            step for step in steps if _is_setup_uv(step) and _caches_by_default(step)
        ]

        for step in caching:
            given = (step.get("with") or {}).get("prune-cache")
            assert _is_true(given), (
                f"{name}:{job_id}: {step.get('name')!r} may save a uv cache "
                f"without prune-cache: true, got {given!r}"
            )


class TestWorkflowsPruneAtRestore:
    """A workflow must clean a restored cache before its direct uv use."""

    @pytest.mark.parametrize(("name", "job_id", "steps"), _workflow_uv_jobs())
    def test_workflow_prunes_before_its_first_uv_run(
        self, name: str, job_id: str, steps: list[dict[str, typ.Any]]
    ) -> None:
        """A lane-level or earlier local-action prune must precede direct use."""
        direct_uv_indexes = _non_prune_uv_run_indexes(steps)
        if not direct_uv_indexes:
            return

        first_uv_run = min(direct_uv_indexes)
        caching_setup_indexes = [
            index
            for index, step in enumerate(steps)
            if _is_setup_uv(step) and _caches_by_default(step)
        ]
        if not caching_setup_indexes:
            return

        prior_setup_indexes = [
            index for index in caching_setup_indexes if index < first_uv_run
        ]
        if not prior_setup_indexes:
            return

        restore_prune_indexes = _prune_indexes(steps)
        restore_prune_indexes.extend(
            _local_action_indexes(steps, _local_actions_pruning_cache())
        )
        latest_setup = max(prior_setup_indexes)
        has_prior_prune = any(
            latest_setup < index < first_uv_run for index in restore_prune_indexes
        )
        assert has_prior_prune, (
            f"{name}:{job_id}: no restore-time prune follows the cache-writing "
            f"setup-uv at step {latest_setup} and precedes the first direct uv "
            f"run at step {first_uv_run}"
        )


class TestActionsPruneAtRestore:
    """A composite action that runs `uv` must clean what the caller restored."""

    @pytest.mark.parametrize("action_id", _action_ids())
    def test_each_action_prunes_before_its_first_uv_run(self, action_id: str) -> None:
        """A prune step exists between `setup-uv` and the first `uv` run.

        Position is the property, not presence: an environment restored by
        the calling job is only dangerous if the action invokes `uv` before
        removing it, so a prune placed after the first script would pass a
        mere presence check and fix nothing.
        """
        manifest = REPOSITORY_ROOT / ".github" / "actions" / action_id / "action.yml"
        steps = _steps(load_workflow(manifest))
        _validate_action_prune_order(action_id, steps)

    def test_pruning_before_setup_uv_is_rejected(self) -> None:
        """Reject a restored-cache prune placed before setup-uv."""
        steps = [
            {
                "name": PRUNE_STEP,
                "run": "bash .github/scripts/prune_uv_cache.sh",
            },
            {"uses": "astral-sh/setup-uv@v6"},
            {"run": "uv run --script check.py"},
        ]

        with pytest.raises(AssertionError, match="must follow the last setup-uv"):
            _validate_action_prune_order("prune-before-setup", steps)

    def test_pruning_after_setup_uv_is_accepted(self) -> None:
        """Accept the required setup-uv, prune, then uv-use sequence."""
        steps = [
            {"uses": "astral-sh/setup-uv@v6"},
            {
                "name": PRUNE_STEP,
                "run": "bash .github/scripts/prune_uv_cache.sh",
            },
            {"run": "uv run --script check.py"},
        ]

        _validate_action_prune_order("valid-order", steps)

    def test_pruning_is_required_when_setup_uv_is_inherited(self) -> None:
        """Keep the prune-before-use contract for actions without setup-uv."""
        steps = [
            {
                "name": PRUNE_STEP,
                "run": "bash .github/scripts/prune_uv_cache.sh",
            },
            {"run": "uv run --script check.py"},
        ]

        _validate_action_prune_order("inherits-setup-uv", steps)

    @staticmethod
    def _generated_steps(step_kinds: list[str]) -> list[dict[str, str]]:
        """Translate generated step names into manifest-shaped steps."""
        steps_by_kind = {
            "setup": {"uses": f"{SETUP_UV_PREFIX}v6"},
            "prune": {
                "name": PRUNE_STEP,
                "run": "bash .github/scripts/prune_uv_cache.sh",
            },
            "uv": {"run": "uv run --script check.py"},
            "other": {"run": "echo ready"},
        }
        return [steps_by_kind[kind].copy() for kind in step_kinds]

    @staticmethod
    def _generated_sequence_is_valid(step_kinds: list[str]) -> bool:
        """Return the ordering oracle's result for one generated sequence."""
        uv_indexes = [index for index, kind in enumerate(step_kinds) if kind == "uv"]
        if not uv_indexes:
            return True

        prune_indexes = [
            index for index, kind in enumerate(step_kinds) if kind == "prune"
        ]
        if not prune_indexes:
            return False

        first_prune = min(prune_indexes)
        if first_prune >= min(uv_indexes):
            return False

        last_setup = max(
            (index for index, kind in enumerate(step_kinds) if kind == "setup"),
            default=-1,
        )
        return last_setup < first_prune

    @given(
        step_kinds=st.lists(
            st.sampled_from(("setup", "prune", "uv", "other")),
            min_size=0,
            max_size=12,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_prune_order_matches_generated_step_sequences(
        self, step_kinds: list[str]
    ) -> None:
        """Match the ordering contract across bounded generated step lists."""
        steps = self._generated_steps(step_kinds)
        expected_valid = self._generated_sequence_is_valid(step_kinds)

        if expected_valid:
            _validate_action_prune_order("generated-order", steps)
        else:
            with pytest.raises(AssertionError):
                _validate_action_prune_order("generated-order", steps)
