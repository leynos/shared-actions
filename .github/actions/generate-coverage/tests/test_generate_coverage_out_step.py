"""Run the generate-coverage ``out`` step through its manifest boundary.

``test_set_outputs.py`` calls ``set_outputs.py`` with its environment built by
hand, which proves the script and not the manifest around it: a manifest that
mapped the suffix input to a different variable, interpolated a value into the
command line unquoted, or renamed the ``GC_`` variables would pass every one of
those. These tests start from the values a caller supplies and run the step's
shipped Bash fragment, so the boundary covered is input to command line to file
written to declared output. The ``env.ACT`` lane runs the same step on a real
runner; this module is what fails first, without a container.

Two properties of the harness shape the copy of the step that runs here, and
both are visible in :func:`_run_out_step`:

* ``github.job`` and ``strategy.job-index`` are not expression names
  ``composite_fragments`` resolves, so those two ``env:`` values are replaced
  by literals carrying the same values the ambient job variables do.
* the step's ``if:`` condition is dropped. The harness parses only an omitted
  condition, a leading ``failure()``, or a single ``==``; the real condition's
  contract -- ``always()`` gated on a successful detection -- is asserted
  against the manifest itself by ``test_archive_masking.py``.

One thing a runner does is done here because the harness does not: a composite
fragment's expressions are rendered before it runs, so the step's
``${{ github.action_path }}`` -- the only expression in its ``run`` text -- is
resolved with the same context the harness resolves the step's ``env:`` block
with. The fragment text itself is the manifest's, and the ``env:`` block keeps
the step's ``GC_OUTPUT_PATH`` and ``GC_ARTEFACT_NAME_SUFFIX`` names with the
step's own ``${{ inputs ... }}`` expressions in their values, so a manifest that
moved a value onto an ``INPUT_``-prefixed name or stopped passing it as an
argument fails here.

Run with the rest of the suite via ``make test``.
"""

from __future__ import annotations

import tempfile
import typing as typ
from pathlib import Path

import pytest
import yaml
from _coverage_test_support import _load_module
from shared_actions_conftest import REQUIRES_UV

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    LifecycleResult,
    ambient_env,
    bash_path,
    require_posix_host,
    run_lifecycle,
)

if typ.TYPE_CHECKING:
    from types import ModuleType

pytestmark = REQUIRES_UV

ACTION_DIR = Path(__file__).resolve().parents[1]
#: The absolute path a metacharacter-bearing value would tell a shell to
#: create. Named once, from the system temporary directory rather than the
#: test's own, so it is one path to look for after every case and a case cannot
#: pass by clearing a directory it owns.
INJECTION_MARKER = Path(tempfile.gettempdir()) / "generate-coverage-out-injected"
#: The command every injection case names, kept in one place so the values and
#: the marker the assertion cleans up cannot drift apart.
_INJECTION = f"touch {INJECTION_MARKER}"
#: The job and index a runner would report for this step. Written as literals
#: because the manifest reaches them through expressions the harness does not
#: resolve; the same values back the ``GITHUB_JOB`` and ``STRATEGY_JOB_INDEX``
#: variables the step's ``set_outputs.py`` reads.
JOB = "coverage"
JOB_INDEX = "2"
#: The output path every case uses unless it is the path that carries the
#: metacharacters. It contains a space so the argument survives only if the
#: manifest quotes its expansion.
OUTPUT_PATH = "reports/coverage report.xml"
#: What ``set_outputs.py`` composes the name from when ``--fmt`` is omitted,
#: which is what happens under act: the step relies on ``DETECTED_FMT``.
FMT = "cobertura"

require_posix_host()


def _manifest() -> dict[str, typ.Any]:
    """Return the parsed ``action.yml``."""
    return yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))


def _out_step() -> dict[str, typ.Any]:
    """Return the manifest's single ``out`` step."""
    steps = _manifest()["runs"]["steps"]
    matches = [step for step in steps if step.get("id") == "out"]
    assert len(matches) == 1, f"expected exactly one `out` step, got {len(matches)}"
    return matches[0]


@pytest.fixture
def set_outputs_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load ``set_outputs`` for the two oracles below.

    The script's own platform detection is the only correct source for the
    name's os and arch components, and its own normalizer the only correct
    source for a suffix segment: it prefers the live platform over the
    ``RUNNER_OS`` variable, so a host that is not Linux would disagree with
    any expectation written from the step's environment.
    """
    return _load_module(monkeypatch, "set_outputs")


def _expected_name(set_outputs_module: ModuleType, suffix: str | None) -> str:
    """Return the artefact name the script should compose for *suffix*."""
    runner_os, runner_arch = set_outputs_module._detect_runner_labels(None, None)
    name = f"{FMT}-{JOB}-{JOB_INDEX}-{runner_os}-{runner_arch}"
    if suffix:
        # Falsiness, not emptiness, is the guard: an omitted or empty suffix
        # adds no segment, while a whitespace-only one is a truthy string that
        # normalizes to nothing and so takes the ``extra`` fallback.
        segment = set_outputs_module._normalize_component(suffix, "extra")
        name = f"{name}-{segment}"
    return name


class Outcome(typ.NamedTuple):
    """What a caller sees after one use of the ``out`` step."""

    result: LifecycleResult
    outputs: dict[str, str]


def _run_out_step(tmp_path: Path, *, output_path: str, suffix: str | None) -> Outcome:
    """Run the manifest's ``out`` step and read the declared outputs.

    Parameters
    ----------
    tmp_path
        Scratch directory the fragment runs in.
    output_path
        The caller's ``output-path`` value.
    suffix
        The caller's ``artefact-name-suffix`` value; ``None`` means the input
        was omitted, which resolves to the empty string a runner substitutes
        for an input that declares no default.

    Returns
    -------
    Outcome
        The step result and the action's outputs as a caller reads them.
    """
    declared = _manifest()["inputs"]
    inputs = {name: str(spec.get("default", "")) for name, spec in declared.items()}
    inputs["output-path"] = output_path
    if suffix is not None:
        inputs["artefact-name-suffix"] = suffix
    context = ActionContext(
        inputs=inputs,
        runner_os="Linux",
        runner_arch="X64",
        action_path=bash_path(ACTION_DIR),
        step_outputs={"detect": {"fmt": FMT}},
    )
    step = _out_step()
    env = dict(step["env"])
    # The two expressions the harness cannot resolve, bound to the values the
    # job-level variables carry in this module's scope.
    env["GITHUB_JOB"] = JOB
    env["STRATEGY_JOB_INDEX"] = JOB_INDEX
    # Every ambient `INPUT_*` name is dropped first. nektos/act exports each
    # declared input under its dashed name, so a developer running this suite
    # under act has those variables already; dropping them keeps the
    # omitted-suffix case free of an input nothing in this module supplied.
    # The step must still read its values from its own `env:` block and its
    # command line, which is the property under test.
    base_env = {
        key: value
        for key, value in ambient_env().items()
        if not key.startswith("INPUT_")
    }
    curated: dict[str, typ.Any] = {
        **step,
        "env": env,
        "run": context.render(str(step["run"])),
        # This step declares no `name:` of its own -- the manifest labels it
        # only by `id` -- and the harness keys each run fragment by a name, so
        # the identifier stands in for a label the manifest never wrote.
        "name": str(step.get("name") or step["id"]),
    }
    # The condition is the only key left out of the copy; see the module
    # docstring for why, and test_archive_masking for its contract.
    curated.pop("if", None)
    result = run_lifecycle(
        [curated],
        context,
        FragmentEnvironment(
            base_env=base_env, cwd=tmp_path, output_dir=tmp_path / "out"
        ),
    )
    outputs = {
        name: context.render(spec["value"])
        for name, spec in _manifest()["outputs"].items()
    }
    return Outcome(result=result, outputs=outputs)


def _assert_reported_the_callers_values(
    outcome: Outcome,
    set_outputs_module: ModuleType,
    *,
    output_path: str,
    suffix: str | None,
    expected_segment: str | None = None,
) -> None:
    """Assert the step succeeded and reported exactly what it was handed.

    *expected_segment* pins the trailing segment as a written literal. The
    derived oracle below mirrors the implementation, so it cannot catch a
    change to how a value is turned into a segment; a literal can. Pass it
    wherever the case has a truth-table meaning worth freezing.
    """
    combined = outcome.result.stdout + outcome.result.stderr
    assert outcome.result.returncode == 0, (
        f"the out step exited {outcome.result.returncode}\n{combined}"
    )
    assert "specified multiple times" not in combined, combined
    assert outcome.outputs["file"] == output_path
    assert outcome.outputs["format"] == FMT
    reported = outcome.outputs["artefact-name"]
    expected = _expected_name(set_outputs_module, suffix)
    assert reported == expected, (
        "the artefact name a caller reads must be the one composed from the "
        "caller's own inputs"
    )
    if expected_segment is not None:
        assert reported.endswith(expected_segment), (
            f"expected the name to end with {expected_segment!r}, got {reported!r}"
        )


@pytest.mark.parametrize(
    ("suffix", "expected_segment"),
    [
        pytest.param(None, "", id="omitted"),
        pytest.param("", "", id="empty"),
        pytest.param("   ", "-extra", id="whitespace-only"),
        pytest.param(" Feature Nightly ", "-feature-nightly", id="spaces"),
    ],
)
def test_out_step_reports_the_callers_values(
    tmp_path: Path,
    set_outputs_module: ModuleType,
    suffix: str | None,
    expected_segment: str,
) -> None:
    """A path and suffix with spaces travel to the outputs intact.

    Both values pass through the step's ``env:`` block and reach the script as
    quoted expansions, so the space-bearing path is the case that fails if a
    future manifest interpolates them into the command line unquoted. The
    suffix cases are a truth table over what a caller can supply: omitted,
    empty, whitespace-only and real each produce the name the script's own
    normalizer predicts. Omitted and empty add no segment; whitespace-only is
    truthy and so adds the ``extra`` fallback segment, asserted here as a
    written literal so a change to that fallback fails rather than being
    mirrored by the derived oracle.
    """
    outcome = _run_out_step(tmp_path, output_path=OUTPUT_PATH, suffix=suffix)
    _assert_reported_the_callers_values(
        outcome,
        set_outputs_module,
        output_path=OUTPUT_PATH,
        suffix=suffix,
        expected_segment=expected_segment,
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(f"Nightly; {_INJECTION}", id="semicolon"),
        pytest.param(f"Nightly$({_INJECTION})", id="substitution"),
        pytest.param(f"Nightly`{_INJECTION}`", id="backticks"),
        pytest.param(f'Nightly"; {_INJECTION}; echo "', id="quote"),
        pytest.param(f"Nightly'$({_INJECTION})'", id="single-quote"),
        pytest.param(f"Nightly|{_INJECTION}", id="pipe"),
    ],
)
def test_out_step_treats_a_suffix_as_data(
    tmp_path: Path, set_outputs_module: ModuleType, suffix: str
) -> None:
    """A suffix naming a command cannot make the step run it.

    Each value carries a real suffix as well as the command, so the expected
    name proves the value reached the script while the marker's absence proves
    no shell interpreted it. Between them the cases cover the routes a shell
    would take: a separator, a substitution, a quote that would end the
    argument, and a pipeline.
    """
    INJECTION_MARKER.unlink(missing_ok=True)
    outcome = _run_out_step(tmp_path, output_path=OUTPUT_PATH, suffix=suffix)
    try:
        _assert_reported_the_callers_values(
            outcome, set_outputs_module, output_path=OUTPUT_PATH, suffix=suffix
        )
        assert not INJECTION_MARKER.exists(), (
            "a character in the suffix was interpreted by a shell rather than "
            "passed to the script: either the manifest interpolates the input "
            "into the command line, or the value reaches a shell that expands "
            "it"
        )
    finally:
        INJECTION_MARKER.unlink(missing_ok=True)


def test_out_step_treats_an_output_path_as_data(
    tmp_path: Path, set_outputs_module: ModuleType
) -> None:
    """The same holds for the other value the step hands the script.

    ``output-path`` is required, and a caller is free to choose it, so it must
    be just as inert as the suffix. The path is never written by the action --
    only reported -- so the marker cannot appear on any correct run.
    """
    # The trailing `echo <marker>.xml` keeps the value looking like a path a
    # caller would choose while each command creates the same file the
    # assertion below looks for.
    poisoned = f"reports/coverage; {_INJECTION}; echo {INJECTION_MARKER}.xml"
    INJECTION_MARKER.unlink(missing_ok=True)
    outcome = _run_out_step(tmp_path, output_path=poisoned, suffix="Nightly")
    try:
        _assert_reported_the_callers_values(
            outcome, set_outputs_module, output_path=poisoned, suffix="Nightly"
        )
        assert not INJECTION_MARKER.exists(), (
            "a character in the output path was interpreted by a shell rather "
            "than passed to the script"
        )
    finally:
        INJECTION_MARKER.unlink(missing_ok=True)
