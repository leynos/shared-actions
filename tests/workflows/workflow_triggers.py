"""What starts a workflow, read for the main-owned coverage boundary.

Every boundary in the contract is drawn from these readings: the
pull-request closure starts from :func:`starts_on_pull_request`, and the
publisher and the second-writer rule both rest on :func:`pushes_to_main`. A
reader that resolved no triggers would make each of those sets empty and
satisfy every assertion over them, so the readings accept every spelling
GitHub does.
"""

from __future__ import annotations

import fnmatch
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Triggers a pull request can fire.
PULL_REQUEST_EVENTS: typ.Final[frozenset[str]] = frozenset(
    {"pull_request", "pull_request_target"}
)
#: The branch a publisher answers.
TRUNK: typ.Final[str] = "main"


def triggers(document: cabc.Mapping[typ.Any, typ.Any]) -> dict[str, typ.Any]:
    """Return a workflow's triggers, keyed by event name.

    PyYAML resolves an unquoted ``on:`` key to the boolean ``True``, so a
    reader that consults only the string key sees no triggers at all and
    every boundary drawn from it passes over an empty set. Both keys are
    read here, and the three spellings GitHub accepts (a mapping, a list,
    and a bare string) are normalized to a mapping. A list read as a
    mapping key would stringify into one event named after the whole list,
    which matches nothing.

    Examples
    --------
    >>> triggers({True: {"pull_request": None}})
    {'pull_request': None}
    >>> triggers({"on": ["push", "workflow_dispatch"]})
    {'push': None, 'workflow_dispatch': None}
    """
    raw = document.get("on", document.get(True))
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(event): None for event in raw}
    if isinstance(raw, dict):
        return {str(event): value for event, value in raw.items()}
    return {}


def starts_on_pull_request(document: cabc.Mapping[typ.Any, typ.Any]) -> bool:
    """Return whether a pull request can start this workflow directly."""
    return bool(PULL_REQUEST_EVENTS & set(triggers(document)))


def _patterns(raw: object) -> list[str]:
    """Return a branch filter's patterns, whether written as a list or a string."""
    if isinstance(raw, list):
        return [str(pattern) for pattern in raw]
    return [] if raw is None else [str(raw)]


def _selects_trunk(patterns: list[str]) -> bool:
    """Return whether an ordered branch-pattern list selects the trunk.

    GitHub reads the list in order, and a later ``!``-prefixed pattern
    excludes what an earlier one matched, so the last matching pattern
    decides. ``fnmatch`` stands in for GitHub's glob: the two differ on
    ``/`` and on ``+``, and the trunk's name contains neither.
    """
    selected = False
    for pattern in patterns:
        excludes = pattern.startswith("!")
        if fnmatch.fnmatchcase(TRUNK, pattern.removeprefix("!")):
            selected = not excludes
    return selected


def _push_filter_admits_trunk(configuration: cabc.Mapping[str, typ.Any]) -> bool:
    """Return whether a ``push`` trigger's filters let a trunk push through.

    With no branch filter, a push filtered on tags alone runs for no branch
    at all; with no filter of either kind, it runs for every branch.
    """
    if "branches" in configuration:
        return _selects_trunk(_patterns(configuration["branches"]))
    if "branches-ignore" in configuration:
        return not _selects_trunk(_patterns(configuration["branches-ignore"]))
    return not ({"tags", "tags-ignore"} & set(configuration))


def pushes_to_main(document: cabc.Mapping[typ.Any, typ.Any]) -> bool:
    """Return whether a push to ``main`` starts this workflow.

    Examples
    --------
    >>> pushes_to_main({True: {"push": {"branches": ["release/*", "ma*"]}}})
    True
    >>> pushes_to_main({True: {"push": {"tags": ["v*"]}}})
    False
    """
    if "push" not in triggers(document):
        return False
    configuration = triggers(document)["push"]
    if not isinstance(configuration, dict):
        return True
    return _push_filter_admits_trunk(configuration)
