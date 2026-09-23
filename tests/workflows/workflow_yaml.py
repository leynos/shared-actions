"""Parse a workflow the way GitHub reads it, refusing what PyYAML forgives.

PyYAML keeps the last of two identical mapping keys and says nothing. A
workflow declaring ``runs-on`` twice therefore parses into a document that
discarded the first value, and every contract reading that document judges a
file GitHub never runs. GitHub itself rejects the duplicate, so the contracts
refuse it too, rather than reason about whichever half survived.

This is the one filesystem boundary for the workflow contracts. Enumerating,
reading and parsing a workflow each fail as a ``ValueError`` that names the
file or directory, so a caller handles one documented error rather than a
mixture of ``OSError`` and parser exceptions. Other readers of workflow YAML
should parse through :func:`load_workflow` rather than calling
``yaml.safe_load`` directly.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

import yaml

if typ.TYPE_CHECKING:
    from pathlib import Path


class UniqueKeyLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that refuses a mapping declaring one key twice."""

    @typ.override
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[typ.Hashable, typ.Any]:
        """Construct the mapping, raising on the second of two equal keys."""
        seen: set[typ.Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, cabc.Hashable):
                continue
            if key in seen:
                context = "while constructing a mapping"
                problem = f"found duplicate key {key!r}"
                raise yaml.constructor.ConstructorError(
                    context, node.start_mark, problem, key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


#: Both file extensions GitHub reads a workflow from, compared without case so
#: a ``.YML`` file is not skipped in silence.
WORKFLOW_SUFFIXES: typ.Final[frozenset[str]] = frozenset({".yml", ".yaml"})


def workflow_paths(directory: Path) -> list[Path]:
    """Return every workflow file in *directory*, under either extension.

    The directory is listed with ``iterdir`` rather than ``glob``: ``glob``
    returns nothing for a directory it cannot read, which every contract
    over the result would then pass.

    Raises
    ------
    ValueError
        When the directory is missing or cannot be listed. The message names
        it.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as error:
        message = f"cannot list workflows in {directory}: {error}"
        raise ValueError(message) from error
    return [path for path in entries if path.suffix.lower() in WORKFLOW_SUFFIXES]


def read_workflow_text(path: Path) -> str:
    """Return the text of the workflow at *path*.

    Raises
    ------
    ValueError
        When the file cannot be read. The message names it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"cannot read workflow {path.name}: {error}"
        raise ValueError(message) from error


def load_workflow(path: Path) -> object:
    """Return the parsed workflow at *path*.

    Raises
    ------
    ValueError
        When the file cannot be read, is not valid YAML, or declares a key
        twice. The message names the file, which the parser's own error does
        not.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as directory:
    ...     path = pathlib.Path(directory, "ci.yml")
    ...     _ = path.write_text("jobs: {}", encoding="utf-8")
    ...     load_workflow(path)
    {'jobs': {}}
    """
    text = read_workflow_text(path)
    try:
        # S506 cannot see through the subclass; the loader is a SafeLoader.
        return yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506
    except yaml.YAMLError as error:
        message = f"{path.name} is not a workflow GitHub would read: {error}"
        raise ValueError(message) from error
