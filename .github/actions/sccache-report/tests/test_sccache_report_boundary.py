"""Run `sccache-report` through its composite-action boundary.

`test_sccache_report.py` runs the report step's script with the `SR_*`
variables set directly, which proves the script and not the manifest around it:
a manifest that mapped `stats-file` to the wrong variable, or published the
wrong step output, would pass every one of those. These tests start from what a
caller writes. A `with:` mapping is resolved against the manifest's declared
inputs and defaults, the step's `env:` block is rendered from it, the step runs
under the shared composite-fragment harness against a controlled `sccache`, and
the action's declared `outputs:` are read the way a caller's `steps.<id>.outputs`
would be.

That is the caller-visible boundary: input to environment, files written, job
summary, and output propagation, for a normal run, a custom path, a fallback and
a missing binary. It stops short of a GitHub-hosted runner, which a unit test
cannot arrange.

Run with the rest of the suite via ``make test``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

from composite_fragments import (
    ActionContext,
    CompositeStep,
    FragmentEnvironment,
    LifecycleResult,
    ambient_env,
    bash_file_path,
    bash_path,
    require_posix_host,
    run_lifecycle,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

ACTION_DIR = Path(__file__).resolve().parents[1]
STUB_JSON = '{"stats":{"compile_requests":7}}'
STUB_TEXT = "Compile requests 7"


class Outcome(typ.NamedTuple):
    """What a caller sees after one use of the action."""

    result: LifecycleResult
    outputs: dict[str, str]
    cwd: Path
    summary: str
    calls: str


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed `action.yml`."""
    return yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))


def _write_stub(directory: Path) -> None:
    """Install a controlled `sccache` that records each call it receives."""
    directory.mkdir()
    stub = directory / "sccache"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$(dirname "$0")/calls.log"\n'
        'if [[ "$*" == *json* ]]; then\n'
        f"  echo '{STUB_JSON}'\n"
        "else\n"
        f'  echo "{STUB_TEXT}"\n'
        "fi\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def use_action(
    tmp_path: Path, with_inputs: dict[str, str] | None = None, *, installed: bool = True
) -> Outcome:
    """Use the action the way a workflow would, and return what the caller sees.

    Parameters
    ----------
    tmp_path : Path
        The working directory and scratch space for the run.
    with_inputs : dict[str, str] | None
        The caller's `with:` mapping. Every key must be a declared input.
    installed : bool
        Whether the controlled `sccache` is on `PATH`.

    Returns
    -------
    Outcome
        The step results, the action's resolved outputs, the working directory,
        the job summary text and the calls the controlled `sccache` received.
    """
    require_posix_host()
    manifest = _manifest()
    declared = manifest["inputs"]
    supplied = with_inputs or {}
    unknown = sorted(set(supplied) - set(declared))
    assert not unknown, f"`with:` names inputs the action does not declare: {unknown}"
    inputs = {name: str(spec.get("default", "")) for name, spec in declared.items()}
    inputs.update(supplied)
    bin_dir = tmp_path / "bin"
    _write_stub(bin_dir)
    summary = tmp_path / "summary"
    summary.write_text("", encoding="utf-8")
    base_env = ambient_env()
    base_env["PATH"] = (
        f"{bash_path(bin_dir)}:/usr/bin:/bin" if installed else "/usr/bin:/bin"
    )
    base_env["GITHUB_STEP_SUMMARY"] = bash_file_path(summary)
    context = ActionContext(
        inputs=inputs,
        runner_os="Linux",
        runner_arch="X64",
        action_path=str(ACTION_DIR),
    )
    steps = typ.cast("list[CompositeStep]", manifest["runs"]["steps"])
    result = run_lifecycle(
        steps,
        context,
        FragmentEnvironment(
            base_env=base_env, cwd=tmp_path, output_dir=tmp_path / "out"
        ),
    )
    outputs = {
        name: context.render(spec["value"])
        for name, spec in manifest["outputs"].items()
    }
    calls_log = bin_dir / "calls.log"
    return Outcome(
        result=result,
        outputs=outputs,
        cwd=tmp_path,
        summary=summary.read_text(encoding="utf-8"),
        calls=calls_log.read_text(encoding="utf-8") if calls_log.exists() else "",
    )


def test_a_custom_stats_file_receives_the_json_and_is_the_output(
    tmp_path: Path,
) -> None:
    """The caller's path, not the default, gets the JSON and is published.

    A manifest that ignored `stats-file` and wrote `sccache-stats.json` would
    pass every test that sets `SR_STATS_FILE` directly.
    """
    outcome = use_action(
        tmp_path, {"stats-file": "custom.json", "text-file": "custom.txt"}
    )

    assert outcome.result.returncode == 0, outcome.result.stderr
    assert outcome.outputs == {"reported": "true", "stats-file": "custom.json"}, (
        "the caller-visible outputs must name the caller's path"
    )
    assert (tmp_path / "custom.json").read_text(encoding="utf-8").strip() == STUB_JSON
    assert STUB_TEXT in (tmp_path / "custom.txt").read_text(encoding="utf-8")
    assert not (tmp_path / "sccache-stats.json").exists(), (
        "the default JSON path must not be used when the caller names another"
    )
    assert not (tmp_path / "sccache-stats.txt").exists(), (
        "the default text path must not be used when the caller names another"
    )


def test_the_defaults_apply_when_the_caller_names_no_paths(tmp_path: Path) -> None:
    """With an empty `with:`, the manifest's own defaults are the paths."""
    outcome = use_action(tmp_path)

    assert outcome.outputs == {"reported": "true", "stats-file": "sccache-stats.json"}
    assert (tmp_path / "sccache-stats.json").read_text(encoding="utf-8").strip() == (
        STUB_JSON
    )
    assert STUB_TEXT in (tmp_path / "sccache-stats.txt").read_text(encoding="utf-8")


def test_the_backend_input_reaches_the_job_summary(tmp_path: Path) -> None:
    """`backend` is named in the summary, with the statistics under it."""
    outcome = use_action(tmp_path, {"backend": "ubicloud"})

    assert "- backend: `ubicloud`" in outcome.summary, outcome.summary
    assert STUB_TEXT in outcome.summary, outcome.summary


def test_summary_false_leaves_the_job_summary_alone(tmp_path: Path) -> None:
    """Only the literal `true` appends to the summary."""
    outcome = use_action(tmp_path, {"summary": "false"})

    assert outcome.summary == "", outcome.summary
    assert outcome.outputs["reported"] == "true", (
        "declining the summary must not decline the report"
    )


@pytest.mark.parametrize("status", ["started", ""])
def test_any_status_but_a_fallback_reports(tmp_path: Path, status: str) -> None:
    """Only `fallback` stands down; `started` and empty report."""
    outcome = use_action(tmp_path, {"status": status})

    assert outcome.outputs["reported"] == "true", outcome.outputs
    assert "--show-stats" in outcome.calls, "the report must ask sccache"


def test_a_fallback_stands_down_without_asking_sccache(tmp_path: Path) -> None:
    """A fallback reports nothing, writes nothing, and the step still succeeds."""
    outcome = use_action(
        tmp_path,
        {"status": "fallback", "stats-file": "custom.json", "text-file": "custom.txt"},
    )

    assert outcome.result.returncode == 0, outcome.result.stderr
    assert outcome.outputs == {"reported": "false", "stats-file": ""}, outcome.outputs
    assert outcome.calls == "", "a fallback must not call sccache at all"
    assert outcome.summary == "", "a fallback must not write to the summary"
    assert not (tmp_path / "custom.json").exists()
    assert not (tmp_path / "custom.txt").exists()
    assert "sccache-report::sccache fell back" in outcome.result.stdout, (
        "the stand-down must say why"
    )


def test_a_missing_sccache_stands_down_without_failing(tmp_path: Path) -> None:
    """With no `sccache` on `PATH` the action reports false and succeeds."""
    outcome = use_action(tmp_path, installed=False)

    assert outcome.result.returncode == 0, outcome.result.stderr
    assert outcome.outputs == {"reported": "false", "stats-file": ""}, outcome.outputs
    assert outcome.summary == ""
    assert "not on PATH" in outcome.result.stdout


def test_an_undeclared_input_is_refused_by_the_harness(tmp_path: Path) -> None:
    """A `with:` key the manifest does not declare must not be silently ignored."""
    with pytest.raises(AssertionError, match="does not declare"):
        use_action(tmp_path, {"stat-file": "typo.json"})


def test_the_job_summary_is_stable(tmp_path: Path, snapshot: SnapshotAssertion) -> None:
    """Snapshot the whole Markdown summary the action writes.

    The heading, the backend line, the spacing and the fenced block are what a
    person reads on the run page. The stand-in's fixed statistics and a fixed
    backend leave nothing nondeterministic in it.
    """
    outcome = use_action(tmp_path, {"backend": "ubicloud"})

    assert outcome.summary == snapshot, "the job summary changed; review the diff"


def test_the_elapsed_metric_is_one_bounded_bucket(tmp_path: Path) -> None:
    """A reporting run logs how long its statistics calls took, in three buckets."""
    outcome = use_action(tmp_path)

    lines = [
        line
        for line in outcome.result.stdout.splitlines()
        if line.startswith("metric sccache-report.elapsed=")
    ]
    assert len(lines) == 1, f"expected one elapsed metric, got {lines}"
    assert lines[0].split("=", 1)[1] in {"lt1s", "lt10s", "ge10s"}, lines
