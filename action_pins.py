"""Commit-SHA pin inventory for this repository's published actions.

A consumer pins one of these actions with a full commit SHA, which fixes the
composite's own revision but says nothing about the references nested inside
it. Those run too. A composite that pins a third-party action to a revision
GitHub later retires fails during **action preparation**, before the first step
of any job runs, and the consumer sees a workflow that passed yesterday fail
today with no commit of its own in between.

Nothing local catches that, because nothing local resolves a pin's transitive
references. So this module makes the transitive set explicit and writes it to
:data:`INVENTORY_PATH`. The inventory is checked in, so it travels with the
revision it describes: a consumer holding ``<action>@<sha>`` can read the
inventory at that same ``<sha>`` and learn what the pin reaches.

Two things are recorded, because they answer different questions:

``actions``
    Keyed by action path. What the action reaches as this checkout stands,
    following first-party references transitively. This is the consumer's
    lookup: their pin resolves to a tree, and this is that tree.

``revisions``
    Keyed by ``<action>@<revision>``. What a specific revision this repository
    references reaches. Used to audit this repository's own pins, including
    ones that do not resolve here — a squash-merged branch head stays out of
    reach of the default branch, so a workflow may name a revision a normal
    clone cannot read. That is recorded as a gap rather than treated as a pass.

Regenerate with ``make action-inventory``; verify with ``main(["--check"])``.
"""

from __future__ import annotations

import dataclasses as dc
import json
import re
import sys
import typing as typ
from pathlib import Path

import yaml
from plumbum import local
from plumbum.commands.processes import CommandNotFound, ProcessExecutionError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Raised when git itself cannot be run. ``CommandNotFound`` is an
#: ``AttributeError`` rather than a :class:`ProcessExecutionError`, and plumbum
#: raises it from the *lookup* as well as the call, so both sites must catch it
#: or a machine without git gets a traceback instead of an unavailable answer.
_GIT_UNAVAILABLE: typ.Final = (ProcessExecutionError, CommandNotFound)

__all__ = [
    "INVENTORY_PATH",
    "NO_COMMENT",
    "RETIRED_REVISIONS",
    "ActionPin",
    "PinError",
    "build_inventory",
    "discover_manifests",
    "first_party_actions",
    "is_shallow",
    "load_inventory",
    "main",
    "pins_in_text",
    "reaches_retired",
    "read_at",
    "reference_path",
    "render_inventory",
    "unknown_references",
    "write_inventory",
]

REPO_ROOT = Path(__file__).resolve().parent
ACTIONS_DIR = REPO_ROOT / ".github" / "actions"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
INVENTORY_PATH = REPO_ROOT / ".github" / "action-inventory.json"

#: The repository-relative prefix a first-party reference is written with.
OWNER = "leynos/shared-actions"

#: A full, immutable commit SHA. A tag, a branch, or a short hash all name a
#: moving target: whoever controls the source can repoint it after review.
_SHA = re.compile(r"[0-9a-f]{40}")

#: One ``uses:`` line, with the optional trailing comment a pin carries to name
#: the release it was measured against. Provenance lives in that comment, so a
#: reader can tell which tag a SHA was resolved from without a network call.
_USES = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[^\s#]+)\s*(?:#\s*(?P<note>.*?))?\s*$")

#: A ``uses:`` key anywhere in a parsed manifest, at any nesting depth.
_USES_KEY = "uses"

#: Recorded for a third-party pin that carries no version comment. The pin is
#: still immutable; what is missing is the human-readable provenance.
NO_COMMENT = "no version comment on the pin"

#: Revisions that must not be referenced again, and why. Every entry is a full
#: ``<action>@<sha>`` reference so the reason travels with the exact object it
#: condemns. Extend this when GitHub retires a release: the contract tests fail
#: until the reference is gone from every manifest, workflow, and inventory.
RETIRED_REVISIONS: typ.Final[dict[str, str]] = {
    "actions/cache@6849a6489940f00c2f30c0fb92c6274307ccb58a": (
        "actions/cache v4.1.2. GitHub retired this release, so any job reaching "
        "it fails during action preparation. A composite that pins it poisons "
        "every consumer, however clean the consumer's own pin looks."
    ),
    f"{OWNER}/.github/actions/upload-codescene-coverage@"
    "395f8e8630d431abb4a136847f1c14c4ad5a0ccc": (
        "Obsolete upload-codescene-coverage revision that pins "
        "actions/cache@6849a648 and so cannot run at all. Its successor is "
        "a5765019912a8ab6882b12db049c7cde635f3a85, which carries the cache "
        "migration. Do not reintroduce it, and do not ship a consumer a pin "
        "that resolves through it."
    ),
}

INVENTORY_SCHEMA = 1


class PinError(RuntimeError):
    """Raised when a pin cannot be resolved or recorded.

    Resolution fails for a reason the caller has to fix — an unknown action, a
    revision this checkout cannot read, or a manifest that will not parse — so
    it is reported rather than skipped. A contract that quietly passes when its
    input is missing is worse than no contract.
    """


@dc.dataclass(frozen=True)
class ActionPin:
    """One ``uses:`` reference, and enough context to find it again.

    Attributes
    ----------
    ref
        The whole reference, as written: ``<action>@<revision>``.
    source
        Repository-relative path of the file the reference was read from.
    line
        1-based line number the reference appears on.
    note
        The pin's trailing comment, which names the release it was measured
        against. Empty when the pin carries no comment.
    """

    ref: str
    source: str
    line: int
    note: str = ""

    @property
    def action(self) -> str:
        """Return the action path, without the revision.

        Returns
        -------
        str
            Everything before the final ``@``, or the whole reference when it
            carries no ``@`` at all.
        """
        head, sep, _ = self.ref.rpartition("@")
        return head if sep else self.ref

    @property
    def revision(self) -> str:
        """Return the revision the reference names.

        Returns
        -------
        str
            Everything after the final ``@``, or an empty string when the
            reference carries none.
        """
        _, sep, tail = self.ref.rpartition("@")
        return tail if sep else ""

    @property
    def is_local(self) -> bool:
        """Return whether the reference names a path inside the checkout.

        Returns
        -------
        bool
            ``True`` for a ``./`` reference, which GitHub resolves from the
            repository running the workflow rather than from a remote.
        """
        return self.ref.startswith("./")

    @property
    def is_immutable(self) -> bool:
        """Return whether the reference is pinned to a full commit SHA.

        Returns
        -------
        bool
            ``True`` when the revision is a bare 40-character hex digest.
        """
        return bool(_SHA.fullmatch(self.revision))

    @property
    def is_first_party(self) -> bool:
        """Return whether the reference names an action of this repository.

        Returns
        -------
        bool
            ``True`` when the action path is this repository's own, which is
            the case a consumer copies into its own workflows.
        """
        return self.action.startswith(f"{OWNER}/")

    def __str__(self) -> str:
        """Return a ``source:line: ref`` form for a failure report.

        Returns
        -------
        str
            A one-line description locating the reference.
        """
        return f"{self.source}:{self.line}: {self.ref}"


def _unquote(value: str) -> str:
    """Return *value* without a matching pair of surrounding quotes.

    A workflow may write a reference as ``uses: "actions/foo@<sha>"``. The
    quotes are YAML syntax, not part of the reference, and leaving them on
    would make an immutable pin look like a floating one.

    Parameters
    ----------
    value
        The raw scalar.

    Returns
    -------
    str
        The scalar with one surrounding quote pair removed, if present.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _yaml_uses(value: object) -> cabc.Iterator[str]:
    """Yield every ``uses:`` value nested anywhere in a parsed document.

    Parameters
    ----------
    value
        A node from ``yaml.safe_load``. Walks dicts and lists, so a reference
        cannot hide behind an ``if``, a nested block, or a fragment.

    Yields
    ------
    str
        Each string found under a ``uses`` key.
    """
    if isinstance(value, dict):
        uses = value.get(_USES_KEY)
        if isinstance(uses, str):
            yield uses
        for nested in value.values():
            yield from _yaml_uses(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _yaml_uses(nested)


def pins_in_text(text: str, *, source: str) -> tuple[ActionPin, ...]:
    """Return every ``uses:`` reference written in *text*.

    A line scan rather than a YAML walk, because the pin's trailing comment is
    part of the record and no YAML loader preserves comments. The walk is what
    catches a reference the scan misses: :func:`_assert_scan_is_complete`
    compares the two and refuses to record a partial answer.

    Parameters
    ----------
    text
        The file's contents.
    source
        Repository-relative path, recorded on each pin.

    Returns
    -------
    tuple[ActionPin, ...]
        Pins in the order they appear.
    """
    pins = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _USES.match(line)
        if match is not None:
            pins.append(
                ActionPin(
                    ref=_unquote(match.group("ref")),
                    source=source,
                    line=number,
                    note=(match.group("note") or "").strip(),
                )
            )
    return tuple(pins)


def _assert_scan_is_complete(
    text: str, pins: cabc.Sequence[ActionPin], source: str
) -> None:
    """Fail when the line scan and a YAML walk disagree on the reference set.

    The scan supplies comments; the walk decides whether anything was missed.
    Comparing them turns a silent under-report — the failure that would make
    every downstream contract vacuous — into a loud one.

    Parameters
    ----------
    text
        The file's contents.
    pins
        What the line scan found.
    source
        Repository-relative path, used in the error message.

    Raises
    ------
    PinError
        If the document will not parse, or the two reads disagree.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{source} is not valid YAML: {exc}"
        raise PinError(msg) from exc

    scanned = {pin.ref for pin in pins}
    walked = {_unquote(value) for value in _yaml_uses(document)}
    if scanned != walked:
        msg = (
            f"{source}: the line scan and the YAML walk disagree; "
            f"scan-only={sorted(scanned - walked)} walk-only={sorted(walked - scanned)}"
        )
        raise PinError(msg)


def discover_manifests(*, root: Path = ACTIONS_DIR) -> tuple[Path, ...]:
    """Return every composite action manifest under *root*.

    Recursive, so a manifest nested inside an action's own directory is covered
    too. The sweep is only as good as its reach.

    Parameters
    ----------
    root
        Directory to search. Defaults to this repository's actions root.

    Returns
    -------
    tuple[Path, ...]
        Absolute manifest paths, sorted for a stable report.

    Raises
    ------
    PinError
        If the sweep finds nothing, which would make every contract vacuous.
    """
    manifests = tuple(
        sorted(
            path
            for suffix in ("action.yml", "action.yaml")
            for path in root.rglob(suffix)
        )
    )
    if not manifests:
        msg = f"no composite action manifests found under {root}"
        raise PinError(msg)
    return manifests


def first_party_actions(
    *, root: Path = ACTIONS_DIR, repo_root: Path = REPO_ROOT
) -> dict[str, Path]:
    """Return this repository's published actions, keyed by reference prefix.

    The key is the full action path as a consumer writes it in ``uses:``, so an
    inventory lookup is the consumer's own text with no rewriting. An action is
    identified by the directory holding its manifest.

    Parameters
    ----------
    root
        Directory holding one subdirectory per action.
    repo_root
        Repository root, used to derive the repository-relative action path.

    Returns
    -------
    dict[str, Path]
        Each ``leynos/shared-actions/.github/actions/<name>`` mapped to its
        manifest path.

    Raises
    ------
    PinError
        If a manifest sits at a depth the key derivation cannot express. The
        published layout is ``<root>/<name>/action.yml``; anything else would
        be keyed wrongly rather than skipped, so it is refused.
    """
    actions: dict[str, Path] = {}
    for manifest in discover_manifests(root=root):
        relative = manifest.relative_to(root)
        if len(relative.parts) != 2:
            msg = (
                f"{manifest} is not at <actions-root>/<name>/<manifest>; "
                f"cannot derive an action path from it"
            )
            raise PinError(msg)
        directory = manifest.parent.relative_to(repo_root).as_posix()
        actions[f"{OWNER}/{directory}"] = manifest
    return actions


def reference_path(action: str) -> str:
    """Return the repository-relative file a first-party reference names.

    Two shapes occur. An action reference names a directory, whose manifest is
    the file that runs. A reusable-workflow reference names the workflow file
    itself. Appending ``/action.yml`` to the second shape would build a path
    that cannot exist, so the two are told apart by whether the reference
    already names a YAML file.

    Parameters
    ----------
    action
        A first-party reference with the revision removed, such as
        ``leynos/shared-actions/.github/actions/setup-rust`` or
        ``leynos/shared-actions/.github/workflows/ci.yml``.

    Returns
    -------
    str
        The path, without the ``leynos/shared-actions/`` prefix.

    Raises
    ------
    PinError
        If *action* is not one of this repository's own references.
    """
    prefix = f"{OWNER}/"
    if not action.startswith(prefix):
        msg = f"{action} is not a {OWNER} reference"
        raise PinError(msg)
    path = action[len(prefix) :]
    if Path(path).suffix in {".yml", ".yaml"}:
        return path
    return f"{path}/action.yml"


def read_at(revision: str, source: str, *, root: Path = REPO_ROOT) -> str | None:
    """Return *source* as it stood at *revision*, or ``None`` when unreadable.

    Resolution reads the local object database rather than the network, so it
    works offline and in CI. A revision the checkout does not have is reported
    as unavailable instead of being guessed at.

    Parameters
    ----------
    revision
        The commit SHA to read from.
    source
        Repository-relative path within that revision.
    root
        Repository root to run git in.

    Returns
    -------
    str | None
        The file's contents, or ``None`` when git cannot read it.
    """
    try:
        return local["git"]["show", f"{revision}:{source}"](cwd=str(root))
    except _GIT_UNAVAILABLE:
        return None


def is_shallow(*, root: Path = REPO_ROOT) -> bool:
    """Return whether this checkout lacks the history the walk needs.

    A shallow clone cannot resolve an arbitrary revision, so every first-party
    reference would record as a gap and the inventory would describe the
    clone's depth rather than the repository. Callers that build or verify one
    must know that before trusting the result.

    Parameters
    ----------
    root
        Repository root to run git in.

    Returns
    -------
    bool
        ``True`` when git reports a shallow repository. A checkout whose depth
        cannot be established is reported as shallow, so an unreadable state
        fails closed rather than producing an inventory nobody can vouch for.
    """
    try:
        completed = local["git"]["rev-parse", "--is-shallow-repository"](cwd=str(root))
    except _GIT_UNAVAILABLE:
        return True
    return completed.strip() == "true"


def _resolve_uses(
    text: str, source: str, *, root: Path, seen: frozenset[tuple[str, str]]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return the references in *text*, first-party ones followed transitively.

    A first-party reference this checkout cannot read is not an error here. It
    is a fact about the checkout: whichever revision named it is older than,
    or off to the side of, the branch being read. Reporting it as a gap keeps
    the rest of the inventory useful, and keeps the gap visible to whoever
    needs to judge it.

    Parameters
    ----------
    text
        The manifest's contents.
    source
        Repository-relative path, recorded on each pin.
    root
        Repository root to resolve against.
    seen
        The ``(action, revision)`` pairs already on this path, extended as the
        walk descends.

    Returns
    -------
    tuple[dict[str, str], dict[str, str]]
        The leaf references reached, mapped to their pin comments or to
        :data:`NO_COMMENT`; then the unresolvable ``action@revision``
        references, mapped to the file that named them.

    Raises
    ------
    PinError
        If the manifest will not parse, or the references form a cycle.
    """
    pins = pins_in_text(text, source=source)
    _assert_scan_is_complete(text, pins, source)

    uses: dict[str, str] = {}
    gaps: dict[str, str] = {}
    for pin in pins:
        if not pin.is_first_party:
            uses[pin.ref] = pin.note or NO_COMMENT
            continue
        key = (pin.action, pin.revision)
        if key in seen:
            msg = f"cycle in first-party references: {pin.action}@{pin.revision}"
            raise PinError(msg)
        nested = read_at(pin.revision, reference_path(pin.action), root=root)
        if nested is None:
            gaps[f"{pin.action}@{pin.revision}"] = source
            continue
        deeper, deeper_gaps = _resolve_uses(
            nested, source, root=root, seen=seen | {key}
        )
        uses.update(deeper)
        gaps.update(deeper_gaps)
    return uses, gaps


def build_inventory(*, root: Path = REPO_ROOT) -> dict[str, typ.Any]:
    """Return the pin inventory for the current checkout.

    Parameters
    ----------
    root
        Repository root. Defaults to this module's repository.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable document with ``schema``, ``retired``, ``actions``,
        and ``revisions`` members.

    Raises
    ------
    PinError
        If a manifest cannot be parsed, if the references form a cycle, or if
        the checkout is shallow. A first-party reference this checkout cannot
        read is recorded under ``unresolved`` on the record that reaches it,
        not raised: a squash-merged branch head cannot be read from a normal
        clone however recently it was fetched, and a contract that aborts on
        that would be reporting the clone's shape rather than the repository's
        health. A shallow clone is different in kind — it cannot read
        *arbitrary* revisions, so most references would record as gaps and the
        document would describe the clone's depth, not the repository.
    """
    if is_shallow(root=root):
        msg = (
            "refusing to build an inventory from a shallow clone: arbitrary "
            "revisions cannot be resolved, so the result would describe the "
            "clone's depth rather than the repository. Fetch full history "
            "(`git fetch --unshallow`) and retry."
        )
        raise PinError(msg)

    actions_root = root / ".github" / "actions"
    workflows_root = root / ".github" / "workflows"

    actions: dict[str, typ.Any] = {}
    for action, manifest in sorted(
        first_party_actions(root=actions_root, repo_root=root).items()
    ):
        source = str(manifest.relative_to(root))
        uses, gaps = _resolve_uses(
            manifest.read_text(encoding="utf-8"),
            source,
            root=root,
            seen=frozenset(),
        )
        record: dict[str, typ.Any] = {
            "manifest": source,
            "uses": uses,
        }
        if gaps:
            record["unresolved"] = gaps
        actions[action] = record

    revisions: dict[str, typ.Any] = {}
    workflow_files = sorted(
        path for suffix in ("*.yml", "*.yaml") for path in workflows_root.glob(suffix)
    )
    for path in workflow_files:
        source = str(path.relative_to(root))
        for pin in pins_in_text(path.read_text(encoding="utf-8"), source=source):
            if not pin.is_first_party:
                continue
            reference = f"{pin.action}@{pin.revision}"
            nested = read_at(pin.revision, reference_path(pin.action), root=root)
            if nested is None:
                revisions[reference] = {
                    "source": reference_path(pin.action),
                    "referenced_by": source,
                    "unresolved": {
                        reference: source,
                    },
                }
                continue
            uses, gaps = _resolve_uses(
                nested,
                source,
                root=root,
                seen=frozenset({(pin.action, pin.revision)}),
            )
            record = {
                "source": reference_path(pin.action),
                "referenced_by": source,
                "uses": uses,
            }
            if gaps:
                record["unresolved"] = gaps
            revisions[reference] = record

    return {
        "schema": INVENTORY_SCHEMA,
        "_comment": (
            "Generated by action_pins.py; regenerate with "
            "`make action-inventory`. `actions` records what each published "
            "action reaches as this revision stands, transitively, so a "
            "consumer holding a pin can read the inventory at the same SHA and "
            "learn whether the pin reaches a retired action. `revisions` "
            "records the same for revisions this repository itself references. "
            "There is deliberately no generation timestamp or head revision: "
            "the file ships inside the revision it describes, so recording "
            "that revision here would be self-referential, and committing the "
            "file would change the very value it stored."
        ),
        "retired": dict(RETIRED_REVISIONS),
        "actions": actions,
        "revisions": revisions,
    }


def render_inventory(inventory: dict[str, typ.Any]) -> str:
    """Return *inventory* as the exact text to check in.

    A single canonical rendering keeps regeneration from producing a spurious
    diff, so a real change is visible as one.

    Parameters
    ----------
    inventory
        The document from :func:`build_inventory`.

    Returns
    -------
    str
        JSON text, newline-terminated.
    """
    return json.dumps(inventory, indent=2, sort_keys=False) + "\n"


def load_inventory(*, path: Path = INVENTORY_PATH) -> dict[str, typ.Any]:
    """Return the checked-in inventory.

    Parameters
    ----------
    path
        The inventory file.

    Returns
    -------
    dict[str, Any]
        The parsed document.

    Raises
    ------
    PinError
        If the file is missing or will not parse.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        msg = f"{path} is missing; run `make action-inventory` to generate it"
        raise PinError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise PinError(msg) from exc
    if not isinstance(document, dict):
        msg = f"{path} does not contain a JSON object"
        raise PinError(msg)
    return document


def _records_for(inventory: dict[str, typ.Any], reference: str) -> list[typ.Any]:
    """Return the inventory records that describe *reference*.

    Two records can apply. ``revisions`` describes the exact ``action@revision``
    when this repository references it; ``actions`` describes the action as the
    checkout stands, which is what a consumer's pin resolves to because the
    inventory ships at the pinned revision.

    Parameters
    ----------
    inventory
        A document from :func:`build_inventory` or :func:`load_inventory`.
    reference
        A pin, such as
        ``leynos/shared-actions/.github/actions/setup-rust@<sha>``.

    Returns
    -------
    list[Any]
        The matching records, in that order, omitting any that are absent.
    """
    action = reference.rpartition("@")[0] or reference
    found = []
    for section, key in (("revisions", reference), ("actions", action)):
        record = (inventory.get(section) or {}).get(key)
        if isinstance(record, dict):
            found.append(record)
    return found


def reaches_retired(inventory: dict[str, typ.Any], reference: str) -> tuple[str, ...]:
    """Return the retired references *reference* reaches, if any.

    This is the consumer's question. A workflow pin is checked against the
    inventory the publisher shipped, so the answer covers the references nested
    inside the pin — the ones the workflow text never names. A reference no
    record describes reaches nothing the inventory can vouch for; call
    :func:`unknown_references` to tell that apart from a clean result.

    Parameters
    ----------
    inventory
        A document from :func:`build_inventory` or :func:`load_inventory`.
    reference
        A pin to test.

    Returns
    -------
    tuple[str, ...]
        The retired references reached, sorted; empty when none are.
    """
    retired = set(inventory.get("retired") or {})
    nested: set[str] = set()
    for record in _records_for(inventory, reference):
        nested |= set(record.get("uses") or {})
    if reference in retired:
        nested.add(reference)
    return tuple(sorted(nested & retired))


def unknown_references(
    inventory: dict[str, typ.Any], references: cabc.Iterable[str]
) -> tuple[str, ...]:
    """Return references the inventory cannot vouch for.

    A publisher that has not recorded a revision cannot say what it reaches. A
    consumer that treats "no retired dependency found" and "nothing known about
    this pin" as the same answer has a check that passes on its own ignorance,
    so the two are separated here and the caller is expected to care.

    Parameters
    ----------
    inventory
        A document from :func:`build_inventory` or :func:`load_inventory`.
    references
        Pins to look up.

    Returns
    -------
    tuple[str, ...]
        The references no record describes, sorted.
    """
    return tuple(sorted(ref for ref in references if not _records_for(inventory, ref)))


def write_inventory(
    inventory: dict[str, typ.Any], *, path: Path = INVENTORY_PATH
) -> bool:
    """Write *inventory* to *path*, returning whether the file changed.

    Parameters
    ----------
    inventory
        The document from :func:`build_inventory`.
    path
        Destination file.

    Returns
    -------
    bool
        ``True`` when the file was written or changed, ``False`` when it was
        already current.
    """
    rendered = render_inventory(inventory)
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.write_text(rendered, encoding="utf-8")
    return True


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Regenerate or verify the checked-in inventory.

    Parameters
    ----------
    argv
        Command-line arguments. ``--check`` verifies the checked-in inventory
        against the checkout without writing; an optional path argument names
        the file to write or verify, defaulting to :data:`INVENTORY_PATH`.

    Returns
    -------
    int
        ``0`` on success, ``1`` when ``--check`` found the inventory stale or
        missing, or when the checkout cannot support a build at all.
    """
    arguments = list(argv if argv is not None else sys.argv[1:])
    checking = "--check" in arguments
    remaining = [argument for argument in arguments if argument != "--check"]
    target = Path(remaining[0]) if remaining else INVENTORY_PATH

    try:
        inventory = build_inventory()
    except PinError as exc:
        # A shallow clone is the expected way this happens — CI checks some
        # jobs out that way — so it reports as a diagnosis and a distinct
        # message rather than a traceback the operator has to decode.
        print(f"{target}: cannot verify: {exc}", file=sys.stderr)
        return 1

    if not checking:
        state = "written" if write_inventory(inventory, path=target) else "current"
        print(f"{target}: {state}")
        return 0

    if not target.exists():
        print(f"{target}: missing", file=sys.stderr)
        return 1
    if target.read_text(encoding="utf-8") != render_inventory(inventory):
        print(f"{target}: stale; run `make action-inventory`", file=sys.stderr)
        return 1
    print(f"{target}: current")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
