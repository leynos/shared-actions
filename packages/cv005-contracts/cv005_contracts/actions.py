"""Load local composite actions into the shape of the workflows the rules read.

A local action runs in its caller's job, with the caller's secrets, so the
closure follows it as it follows a called workflow. `read_actions` is the
filesystem boundary for actions, as `loading.read_workflows` is for
workflows.
"""

from __future__ import annotations

import typing as typ

from .loading import Document, WorkflowReadingError, entries, load_file, load_workflow

if typ.TYPE_CHECKING:
    from pathlib import Path

#: File names GitHub reads a local action's metadata from.
ACTION_FILES: typ.Final[tuple[str, ...]] = ("action.yml", "action.yaml")


def load_action(text: str) -> Document:
    r"""Parse one local action into the shape of a workflow the rules read.

    A composite action's steps run in its caller's job, so the closure
    rules must read them as they read a called workflow's. The steps become
    one job of a workflow triggered only by `workflow_call`, which no seed
    reading counts, and the rest of the metadata is kept under `action`, so
    a whole-document reading still sees its inputs and any `runs` field.
    Text that `load_workflow` refuses is refused the same way.

    Parameters
    ----------
    text : str
        The action metadata's YAML text.

    Returns
    -------
    Document
        A workflow-shaped document holding the action's steps.

    Raises
    ------
    WorkflowReadingError
        If the action's `runs` field is not a mapping.

    Examples
    --------
    >>> load_action("runs:\n  using: composite\n  steps: [{run: echo}]\n")["jobs"]
    {'action': {'steps': [{'run': 'echo'}]}}

    """
    parsed = load_workflow(text)
    runs = parsed.get("runs")
    if not isinstance(runs, dict):
        message = f"an action's `runs` must be a mapping, not {runs!r}"
        raise WorkflowReadingError(message)
    metadata = {**parsed, "runs": {**runs}}
    action_steps = metadata["runs"].pop("steps", [])
    document: Document = {
        "on": "workflow_call",
        "jobs": {"action": {"steps": action_steps}},
        "action": metadata,
    }
    return document


def read_actions(root: Path) -> dict[str, Document]:
    """Return every local action under `.github/actions`, by its `uses:` path.

    The key is the path a step names after `./`, such as
    `.github/actions/build`, so the closure can look an action up by the
    reference that runs it. An action this reads nothing for is refused
    where it is called, not here, since a repository need hold none, so an
    absent `.github/actions` reads as no actions. Every directory under it is
    listed explicitly: `Path.rglob` would skip one it cannot list, and an
    action there would then drop out of the closure in silence. A directory
    that cannot be listed, or metadata that cannot be read or parsed, raises
    `WorkflowReadingError` naming it.

    Parameters
    ----------
    root : Path
        The repository root.

    Returns
    -------
    dict[str, Document]
        Every local action, workflow-shaped, by its path from `root`.

    """
    directory = root / ".github" / "actions"
    if not _is_present(directory):
        return {}
    paths = _action_files(directory)
    keys = [path.parent.relative_to(root).as_posix() for path in paths]
    return {
        key: load_file(path, load_action, key)
        for key, path in zip(keys, paths, strict=True)
    }


def _is_present(directory: Path) -> bool:
    """Return whether a path exists, refusing any failure but its absence.

    `Path.exists` answers false for some failures other than absence, so a
    directory that could not be checked would read as holding no actions.

    Returns
    -------
    bool
        False only when nothing is at the path.

    Raises
    ------
    WorkflowReadingError
        If the path cannot be checked for a reason other than its absence.

    """
    try:
        directory.stat()
    except FileNotFoundError:
        return False
    except OSError as error:
        message = f"{directory} could not be checked: {error}"
        raise WorkflowReadingError(message) from error
    return True


def _action_files(directory: Path) -> list[Path]:
    """Return every action metadata file under a directory, in path order."""
    found: list[Path] = []
    pending = [directory]
    while pending:
        listed = entries(pending.pop())
        pending.extend(path for path in listed if path.is_dir())
        found.extend(path for path in listed if path.name in ACTION_FILES)
    return sorted(found)

def is_action(name: str) -> bool:
    """Return whether a document key names a local action, not a workflow.

    Workflows are keyed by file name and actions by their path from the
    repository root, so only an action's key holds a `/`.

    Parameters
    ----------
    name : str
        A key of the parsed document set.

    Returns
    -------
    bool
        Whether the key names a local action.

    Examples
    --------
    >>> is_action(".github/actions/build"), is_action("ci.yml")
    (True, False)

    """
    return "/" in name
