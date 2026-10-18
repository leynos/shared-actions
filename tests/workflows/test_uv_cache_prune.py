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
- every composite action that runs `uv` prunes once after `setup-uv` and
  before its first invocation, so an environment restored by an earlier step
  of the calling job is removed before it can be used.

The second matters on its own because `prune-cache` only governs the save.
A one-time restore of an old archive, or a caller that restores the cache
itself, is otherwise untouched.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from . import _workflow_reading as reading
from .workflow_yaml import load_workflow

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
ACTIONS_DIRECTORY: typ.Final[Path] = REPOSITORY_ROOT / ".github" / "actions"

#: The pinned setup-uv revision every caching step uses.
SETUP_UV_PREFIX: typ.Final[str] = "astral-sh/setup-uv@"

#: The step name of the restore-time prune.
PRUNE_STEP: typ.Final[str] = "Prune restored uv cache"

#: No lane is exempt. Measured against uv 0.12.19, `uv run --script` and an
#: ephemeral `uv run --with` both write an `environments-v2` entry, while a
#: plain `uv run` in a project uses the project `.venv` and `uv tool
#: install`/`uv tool run` write none. A lane that only does the latter still
#: shares its cache key with every lane on the runner image, so a poisoned
#: archive written there is restored elsewhere; the input costs nothing and
#: ends that. Exempting a lane would be a code change needing its own
#: evidence, not a silent omission.


def _steps(document: object) -> list[dict[str, typ.Any]]:
    """Return one workflow job's steps, or one action's composite steps."""
    if not isinstance(document, dict):
        message = f"manifest is not a mapping: {document!r}"
        raise TypeError(message)
    if "runs" in document:
        runs = document["runs"]
        if not isinstance(runs, dict):
            message = f"'runs' is not a mapping: {runs!r}"
            raise TypeError(message)
        steps = runs.get("steps")
    else:
        steps = document.get("steps")
    if not isinstance(steps, list):
        message = f"manifest has no step list: {steps!r}"
        raise TypeError(message)
    return typ.cast("list[dict[str, typ.Any]]", steps)


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
    """Return whether *step* runs `uv cache prune`."""
    run = step.get("run")
    if not isinstance(run, str):
        return False
    return any(line.strip() == "uv cache prune" for line in run.splitlines())


def _runs_uv(steps: list[dict[str, typ.Any]]) -> bool:
    """Return whether any step in the list invokes `uv`."""
    return any("uv " in str(step.get("run", "")) for step in steps)


def _action_paths() -> list[Path]:
    """Return every composite action manifest, sorted."""
    return sorted(ACTIONS_DIRECTORY.glob("*/action.yml"))


def _action_ids() -> list[str]:
    """Return the directory name of every composite action, for pytest ids."""
    return [path.parent.name for path in _action_paths()]


def _workflow_uv_jobs() -> list[tuple[str, str, list[dict[str, typ.Any]]]]:
    """Return each (workflow, job id, steps) whose steps run `uv`."""
    found: list[tuple[str, str, list[dict[str, typ.Any]]]] = []
    for name in reading.workflow_names():
        document = reading.load_workflow(name)
        jobs = typ.cast("dict[str, typ.Any]", document)["jobs"]
        for job_id, body in jobs.items():
            steps = body.get("steps") or []
            if _runs_uv(steps):
                found.append((name, job_id, steps))
    return found


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
        setup_indexes = [
            index for index, step in enumerate(steps) if _is_setup_uv(step)
        ]
        if not setup_indexes:
            return
        # The prune step runs `uv cache prune`, so it matches the probe
        # above; excluding it is what makes the comparison about the first
        # invocation that could consume a restored environment.
        uv_run_indexes = [
            index
            for index, step in enumerate(steps)
            if isinstance(step.get("run"), str)
            and "uv " in step["run"]
            and not _prunes(step)
        ]
        if not uv_run_indexes:
            return

        first_uv_run = min(uv_run_indexes)
        prune_indexes = [
            index
            for index, step in enumerate(steps)
            if step.get("name") == PRUNE_STEP and _prunes(step)
        ]

        assert prune_indexes, f"{action_id}: no {PRUNE_STEP!r} step"
        assert min(prune_indexes) < first_uv_run, (
            f"{action_id}: {PRUNE_STEP!r} must precede the first `uv` run at "
            f"step {first_uv_run}, found it at {min(prune_indexes)}"
        )
