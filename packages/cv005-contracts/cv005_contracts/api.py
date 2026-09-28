"""Run the CV-005 contracts over a repository's working tree.

The public surface consumers call. Each rule returns messages; this module
names each message with its clause, so a failure says which rule it broke,
and reading failures stay distinct from violations.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from .actions import read_actions
from .config import Config, load_config
from .credential import check_step_violations, token_scope_violations
from .environment import environment_violations
from .hardening import lane_hardening_violations, publisher_hardening_violations
from .lanes import (
    interpreter_violations,
    pull_request_lane_violations,
    second_writer_violations,
)
from .loading import Document, read_workflows
from .parity import inputs_of, is_true, publisher_lane_violations
from .publisher import COVERAGE_ACTION, action_steps, find_publisher
from .publisher_rules import (
    concurrency_violations,
    condition_violations,
    permissions_violations,
    retired_checksum_violations,
    trigger_violations,
    upload_step_violations,
)
from .reach import pull_request_closure, pull_request_violations
from .wiring import wiring_violations

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

#: The contract families a caller can select.
Family = typ.Literal["pull-request", "publisher", "token", "coverage", "environment"]
FAMILIES: typ.Final[frozenset[str]] = frozenset(typ.get_args(Family))


@dc.dataclass(frozen=True, slots=True)
class Violation:
    """One broken clause.

    Attributes
    ----------
    clause : str
        The clause identifier, such as `publisher.concurrency`.
    message : str
        What the clause found, naming the file or job where it can.

    """

    clause: str
    message: str

    def __str__(self) -> str:
        """Return the violation as one report line."""
        return f"{self.clause}: {self.message}"


class ContractError(AssertionError):
    """Raised by the `assert_*` functions with every violation found."""

    def __init__(self, violations: list[Violation]) -> None:
        """Keep every violation, and summarize them in the message."""
        self.violations = violations
        super().__init__("\n".join(str(item) for item in violations))


def read_tree(repo_root: Path) -> dict[str, Document]:
    """Read a repository's workflows and local actions, strictly.

    Parameters
    ----------
    repo_root : Path
        The repository root.

    Returns
    -------
    dict[str, Document]
        Workflows by file name and local actions by their `uses:` path.

    """
    return read_workflows(repo_root / ".github" / "workflows") | read_actions(repo_root)


def violations(
    repo_root: Path,
    config: Config | None = None,
    only: cabc.Set[str] = FAMILIES,
) -> list[Violation]:
    """Return every violation of the selected contract families.

    Parameters
    ----------
    repo_root : Path
        The repository root.
    config : Config | None, optional
        The repository's parameters; read from `.github/cv005.toml` when
        omitted.
    only : collections.abc.Set[str], optional
        The families to run; every family by default.

    Returns
    -------
    list[Violation]
        Every violation, in family order.

    Raises
    ------
    ValueError
        If `only` names an unknown family.

    """
    if unknown := sorted(set(only) - FAMILIES):
        message = f"unknown contract families: {unknown}"
        raise ValueError(message)
    config = config or load_config(repo_root)
    documents = read_tree(repo_root)
    found: list[Violation] = []
    for family, clause, messages in _clauses(documents, config):
        if family in only:
            found.extend(Violation(clause, message) for message in messages())
    return found


def _clauses(
    documents: dict[str, Document], config: Config
) -> cabc.Iterator[tuple[str, str, cabc.Callable[[], list[str]]]]:
    """Yield each clause's family, identifier and deferred reading."""
    repository = config.repository
    yield (
        "pull-request",
        "pull-request.surface",
        lambda: pull_request_violations(documents, repository),
    )
    name, publisher = find_publisher(documents)
    yield ("publisher", "publisher.name", lambda: _named(name, config.publisher))
    yield ("publisher", "publisher.triggers", lambda: trigger_violations(publisher))
    yield (
        "publisher",
        "publisher.concurrency",
        lambda: concurrency_violations(publisher),
    )
    yield (
        "publisher",
        "publisher.permissions",
        lambda: permissions_violations(publisher),
    )
    yield ("publisher", "publisher.upload", lambda: upload_step_violations(publisher))
    yield ("publisher", "publisher.wiring", lambda: wiring_violations(publisher))
    yield ("publisher", "publisher.conditions", lambda: condition_violations(publisher))
    yield (
        "publisher",
        "publisher.retired-checksum",
        lambda: retired_checksum_violations(documents),
    )
    yield (
        "publisher",
        "publisher.least-privilege",
        lambda: publisher_hardening_violations(publisher),
    )
    yield ("token", "token.check-step", lambda: check_step_violations(publisher))
    yield ("token", "token.scope", lambda: token_scope_violations(publisher))
    closure = pull_request_closure(documents, repository)
    yield (
        "coverage",
        "coverage.pull-request-lane",
        lambda: pull_request_lane_violations(closure),
    )
    yield (
        "coverage",
        "coverage.lane-hardening",
        lambda: lane_hardening_violations(closure),
    )
    yield (
        "coverage",
        "coverage.second-writer",
        lambda: second_writer_violations(documents, name, repository),
    )
    yield (
        "coverage",
        "coverage.selection-parity",
        lambda: publisher_lane_violations(publisher, closure),
    )
    yield (
        "coverage",
        "coverage.selection",
        lambda: _selection_violations(publisher, config.selection),
    )
    if config.interpreter is not None:
        interpreter = config.interpreter
        yield (
            "coverage",
            "coverage.interpreter",
            lambda: interpreter_violations(publisher, interpreter),
        )
    if config.environment:
        yield (
            "environment",
            "environment.placement",
            lambda: environment_violations(documents, repository),
        )


def _named(found: str, expected: str) -> list[str]:
    """Refuse a publisher filed under a name other than the configured one."""
    return [] if found == expected else [f"the publisher is {found}, not {expected}"]


def _selection_violations(publisher: Document, selection: dict[str, str]) -> list[str]:
    """Refuse a ratcheting publisher generator whose inputs differ from the config.

    The configured selection is what the baseline measures, so it binds each
    ratcheting leg; a publisher with no ratcheting leg fails the parity rule.
    """
    ratcheting = [
        inputs_of(step)
        for step in action_steps(publisher, COVERAGE_ACTION)
        if is_true(inputs_of(step).get("with-ratchet"))
    ]
    return [
        f"the publisher's generate-coverage sets {key}={held.get(key)!r}, not {value!r}"
        for held in ratcheting or [{}]
        for key, value in sorted(selection.items())
        if str(held.get(key)) != value
    ]


def assert_publisher_contract(repo_root: Path, config: Config | None = None) -> None:
    """Raise unless every CV-005 publisher, token, coverage and surface clause holds.

    Parameters
    ----------
    repo_root : Path
        The repository root.
    config : Config | None, optional
        The repository's parameters; read from `.github/cv005.toml` when
        omitted.

    Raises
    ------
    ContractError
        With every violation found.

    """
    found = violations(repo_root, config, FAMILIES - {"environment"})
    if found:
        raise ContractError(found)


def assert_environment_contract(repo_root: Path, config: Config | None = None) -> None:
    """Raise unless the `codescene` environment placement holds.

    Parameters
    ----------
    repo_root : Path
        The repository root.
    config : Config | None, optional
        The repository's parameters; read from `.github/cv005.toml` when
        omitted.

    Raises
    ------
    ContractError
        With every violation found.

    """
    found = violations(repo_root, config, frozenset({"environment"}))
    if found:
        raise ContractError(found)
