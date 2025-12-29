#!/usr/bin/env -S uv run --script
# fmt: off
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "syspath-hack>=0.3.0,<0.4.0",
# ]
# ///
# fmt: on

"""Derive release workflow modes for GitHub Actions.

The release workflow combines reusable invocations (``workflow_call``) with tag
pushes. This helper normalises the event payload into the booleans the rest of
our workflow needs and emits them via ``GITHUB_OUTPUT``.

Examples
--------
Running the helper during a dry run disables publishing and workflow artefact
uploads::

    $ GITHUB_EVENT_NAME=workflow_call \
      GITHUB_EVENT_PATH=event.json \
      GITHUB_OUTPUT=outputs \
      python determine_release_modes.py

    $ cat outputs
    dry_run=true
    should_publish=false
    should_upload_workflow_artifacts=false
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import json
import os
import typing as typ
from pathlib import Path

from syspath_hack import prepend_project_root

# Add project root for bool_utils import
prepend_project_root()

from bool_utils import coerce_bool

_INPUT_DRIVEN_EVENTS = {"workflow_call", "pull_request"}


@dc.dataclass(frozen=True)
class ReleaseModes:
    """Aggregate release settings derived from the workflow event."""

    dry_run: bool
    should_publish: bool
    should_upload_workflow_artifacts: bool

    def to_output_mapping(self) -> dict[str, str]:
        """Serialise the release modes into ``GITHUB_OUTPUT`` assignments."""
        return {
            "dry_run": _format_bool(value=self.dry_run),
            "should_publish": _format_bool(value=self.should_publish),
            "should_upload_workflow_artifacts": _format_bool(
                value=self.should_upload_workflow_artifacts
            ),
        }


def _determine_dry_run(event_name: str, inputs: cabc.Mapping[str, typ.Any]) -> bool:
    """Determine the dry-run flag from environment override or event inputs."""
    input_dry_run = os.environ.get("INPUT_DRY_RUN", "").strip()
    dry_run_default = event_name == "pull_request"

    if input_dry_run:
        return coerce_bool(input_dry_run, default=dry_run_default)
    return coerce_bool(inputs.get("dry-run"), default=dry_run_default)


def _determine_should_publish(
    event_name: str, inputs: cabc.Mapping[str, typ.Any], *, dry_run: bool
) -> bool:
    """Determine the should-publish flag from event type and inputs."""
    if dry_run:
        return False
    if event_name == "push":
        return True

    input_publish = os.environ.get("INPUT_PUBLISH", "").strip()
    if input_publish:
        return coerce_bool(input_publish, default=False)
    return coerce_bool(inputs.get("publish"), default=False)


def determine_release_modes(
    event_name: str, event: cabc.Mapping[str, typ.Any]
) -> ReleaseModes:
    """Derive release modes from a GitHub Actions event payload.

    Parameters
    ----------
    event_name
        The ``github.event_name`` value describing how the workflow was
        triggered.
    event
        The loaded JSON payload from ``GITHUB_EVENT_PATH``.

    Returns
    -------
    ReleaseModes
        A frozen dataclass describing whether the workflow is a dry run, should
        publish to a release, and may upload workflow artefacts.

    Raises
    ------
    ValueError
        If the ``event_name`` is unsupported or the event inputs contain values
        that cannot be coerced to booleans.

    Examples
    --------
    A tag push always publishes and uploads artefacts::

        >>> determine_release_modes("push", {})
        ReleaseModes(dry_run=False, should_publish=True,
        ... should_upload_workflow_artifacts=True)

    A dry-run workflow call disables publishing and artefact uploads::

        >>> determine_release_modes(
        ...     "workflow_call", {"inputs": {"dry-run": "true", "publish": "true"}}
        ... )
        ReleaseModes(dry_run=True, should_publish=False,
        ... should_upload_workflow_artifacts=False)

    Pull request invocations default to dry-run mode, ensuring artefacts remain
    unpublished::

        >>> determine_release_modes("pull_request", {})
        ReleaseModes(dry_run=True, should_publish=False,
        ... should_upload_workflow_artifacts=False)
    """
    if event_name not in {"push", *_INPUT_DRIVEN_EVENTS}:
        msg = f"Unsupported event '{event_name}' for release workflow"
        raise ValueError(msg)

    inputs = _extract_inputs(event) if event_name in _INPUT_DRIVEN_EVENTS else {}
    dry_run = _determine_dry_run(event_name, inputs)
    should_publish = _determine_should_publish(event_name, inputs, dry_run=dry_run)

    return ReleaseModes(
        dry_run=dry_run,
        should_publish=should_publish,
        should_upload_workflow_artifacts=not dry_run,
    )


def _require_env(var: str) -> str:
    """Return the value of an environment variable, raising if unset."""
    try:
        return os.environ[var]
    except KeyError as exc:
        msg = f"{var} environment variable must be set"
        raise RuntimeError(msg) from exc


def main() -> None:
    """Entry point for GitHub Actions steps."""
    event_name = _require_env("GITHUB_EVENT_NAME")
    event_path = Path(_require_env("GITHUB_EVENT_PATH")).resolve()
    output_path = Path(_require_env("GITHUB_OUTPUT"))
    event_payload = _load_event(event_path)

    modes = determine_release_modes(event_name, event_payload)
    _write_outputs(output_path, modes)

    print(f"Release modes: {modes}")


def _load_event(event_path: Path) -> dict[str, typ.Any]:
    """Load the JSON payload for the triggering event."""
    if not event_path.exists():
        return {}
    with event_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _extract_inputs(event: cabc.Mapping[str, typ.Any]) -> cabc.Mapping[str, typ.Any]:
    """Extract workflow inputs, tolerating empty payloads."""
    inputs = event.get("inputs", {})
    if isinstance(inputs, cabc.Mapping):
        return inputs
    msg = "workflow inputs must be a mapping"
    raise ValueError(msg)


def _format_bool(*, value: bool) -> str:
    """Convert ``bool`` values into the lowercase strings Actions expects."""
    return "true" if value else "false"


def _write_outputs(output_path: Path, modes: ReleaseModes) -> None:
    """Append release mode outputs for the surrounding workflow."""
    mapping = modes.to_output_mapping()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for key, value in mapping.items():
            handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
