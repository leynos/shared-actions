"""Repository-wide contract for third-party pins and the action inventory.

Three properties are enforced here, all of them about references that resolve
*through* a pin rather than being the pin itself:

1. Every third-party reference in a composite manifest is pinned to a full
   commit SHA. A tag is a moving target: it can be repointed after review.
2. No manifest, workflow, or inventory reaches a revision GitHub has retired.
   Retiring a release breaks the jobs that reach it during action preparation,
   before any step runs, with no commit in between — so the reference has to be
   gone from the source, not merely noted.
3. The checked-in inventory matches the checkout, so what a consumer reads is
   what the revision they hold actually reaches.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

import pytest
import yaml

import action_pins
from action_pins import (
    RETIRED_REVISIONS,
    ActionPin,
    discover_manifests,
    pins_in_text,
    reaches_retired,
    reference_path,
    unknown_references,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_ROOT = REPO_ROOT / ".github" / "workflows"

#: A reference that is not this repository's own, and so must be pinned by SHA.
#: A ``./`` reference is resolved from the calling repository and is not a
#: remote at all, which is why it is excluded rather than pinned.
THIRD_PARTY_EXCLUSIONS = "./"


def _manifests() -> list[Path]:
    """Return every composite manifest, found by the module under test."""
    return list(discover_manifests(root=REPO_ROOT / ".github" / "actions"))


def _workflows() -> list[Path]:
    """Return every workflow file, across both YAML spellings."""
    return sorted(
        path for suffix in ("*.yml", "*.yaml") for path in WORKFLOWS_ROOT.glob(suffix)
    )


def _pins(path: Path) -> list[ActionPin]:
    """Return the pins written in *path*."""
    return list(
        pins_in_text(
            path.read_text(encoding="utf-8"),
            source=str(path.relative_to(REPO_ROOT)),
        )
    )


def _third_party_pins(path: Path) -> list[ActionPin]:
    """Return the pins in *path* that must be immutable."""
    return [pin for pin in _pins(path) if not pin.is_first_party and not pin.is_local]


def _require_history() -> None:
    """Skip when the checkout cannot resolve the revisions a rebuild reads.

    Two of the checks below rebuild the inventory, which resolves each pin's
    referenced `action.yml` out of the object database. A shallow clone cannot
    read an arbitrary revision, so every reference would record as a gap and
    the rebuild would describe the clone's depth rather than this repository.

    This is a genuine skip, not a silent pass: the `coverage` job checks out
    with `fetch-depth: 0` and runs the same `testpaths`, so the contract is
    still enforced on every pull request. Only the shallow `python-tests` legs
    step over it.
    """
    if action_pins.is_shallow():
        pytest.skip("shallow checkout cannot resolve pinned revisions")


@pytest.mark.parametrize(
    "manifest",
    _manifests(),
    ids=lambda manifest: str(manifest.relative_to(REPO_ROOT)),
)
def test_third_party_references_are_sha_pinned(manifest: Path) -> None:
    """No composite may reach a third-party action through a floating tag."""
    unpinned = [str(pin) for pin in _third_party_pins(manifest) if not pin.is_immutable]

    assert not unpinned, f"unpinned third-party references: {unpinned}"


@pytest.mark.parametrize(
    "workflow",
    _workflows(),
    ids=lambda workflow: str(workflow.relative_to(REPO_ROOT)),
)
def test_workflow_third_party_references_are_sha_pinned(workflow: Path) -> None:
    """No workflow may reach a third-party action through a floating tag."""
    unpinned = [str(pin) for pin in _third_party_pins(workflow) if not pin.is_immutable]

    assert not unpinned, f"unpinned third-party references: {unpinned}"


def test_the_pin_sweep_covers_the_actions_that_have_third_party_references() -> None:
    """Guard the discovery itself, so a broken sweep cannot pass silently.

    Without this, a manifest walk that returned nothing — a renamed directory,
    a changed suffix — would satisfy every check above vacuously.
    """
    users = {
        manifest.parent.name for manifest in _manifests() if _third_party_pins(manifest)
    }

    assert {
        "generate-coverage",
        "ratchet-coverage",
        "setup-rust",
        "upload-codescene-coverage",
        "windows-package",
    } <= users, f"pin sweep missed expected actions; found {sorted(users)}"


def test_retired_revisions_are_named_as_full_references() -> None:
    """Each retired entry must name one exact object, so the reason sticks.

    A bare SHA could belong to any action; the entry has to say which action
    and which revision together, or the next reader cannot tell what is banned.
    """
    malformed = [
        reference
        for reference in RETIRED_REVISIONS
        if "@" not in reference or not ActionPin(reference, "", 0).is_immutable
    ]

    assert not malformed, f"retired entries are not full references: {malformed}"


def test_the_retired_inventory_covers_the_known_cache_retirement() -> None:
    """The retirement that motivated this contract must be listed.

    ``actions/cache@6849a6489940f00c2f30c0fb92c6274307ccb58a`` is v4.1.2, which
    GitHub retired. Removing it from this list would silently re-permit it.
    """
    assert "actions/cache@6849a6489940f00c2f30c0fb92c6274307ccb58a" in (
        RETIRED_REVISIONS
    )


def test_no_manifest_reaches_a_retired_revision() -> None:
    """A retired revision in a composite poisons every consumer of it.

    This is the case the estate was bitten by: the consumer's own pin was
    immaculate, and the failure arrived from a revision nested inside it.
    """
    offenders = [
        f"{pin.source}:{pin.line}: {pin.ref}"
        for manifest in _manifests()
        for pin in _pins(manifest)
        if pin.ref in RETIRED_REVISIONS
    ]

    assert not offenders, f"manifests reach retired revisions: {offenders}"


def test_no_workflow_reaches_a_retired_revision() -> None:
    """A retired revision in a workflow breaks the job that names it."""
    offenders = [
        f"{pin.source}:{pin.line}: {pin.ref}"
        for workflow in _workflows()
        for pin in _pins(workflow)
        if pin.ref in RETIRED_REVISIONS
    ]

    assert not offenders, f"workflows reach retired revisions: {offenders}"


def test_no_manifest_reaches_a_retired_revision_transitively() -> None:
    """The check that a text scan cannot make: follow the pin to its tree.

    A manifest can be clean of a retired reference and still reach one, because
    the reference it named resolves to a revision carrying the retired action
    several levels down. Only resolving the references finds that.
    """
    _require_history()
    inventory = action_pins.build_inventory()
    offenders: dict[str, tuple[str, ...]] = {}
    for action, record in inventory["actions"].items():
        nested: set[str] = set()
        for reference in record.get("uses", {}):
            nested |= set(reaches_retired(inventory, reference))
        if action in RETIRED_REVISIONS:
            nested.add(action)
        if nested:
            offenders[action] = tuple(sorted(nested))

    assert not offenders, f"actions reach retired revisions: {offenders}"


def test_the_checked_in_inventory_is_current() -> None:
    """The inventory must describe the checkout it ships with.

    A stale inventory is worse than none: a consumer reads it to decide a pin
    is safe, and a stale file can certify a revision it no longer describes.
    """
    _require_history()
    built = action_pins.render_inventory(action_pins.build_inventory())
    checked_in = action_pins.INVENTORY_PATH.read_text(encoding="utf-8")

    assert built == checked_in, (
        f"{action_pins.INVENTORY_PATH} is stale; run `make action-inventory`"
    )


def test_a_job_that_can_resolve_pins_still_runs_these_checks() -> None:
    """The skip above is only honest while a deep checkout runs this suite.

    ``_require_history`` steps over the rebuilding checks in a shallow clone,
    on the grounds that the ``coverage`` job's ``fetch-depth: 0`` checkout
    still enforces them. Remove that checkout depth and the checks stop
    running anywhere while every gate stays green — so the premise is asserted
    here rather than assumed.
    """
    workflow = yaml.safe_load((WORKFLOWS_ROOT / "ci.yml").read_text(encoding="utf-8"))

    depths = [
        str(step.get("with", {}).get("fetch-depth", ""))
        for spec in workflow["jobs"].values()
        for step in spec.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]

    assert "0" in depths, (
        "no job checks out full history, so `_require_history` skips the "
        "inventory rebuilds everywhere and the contract enforces nothing"
    )


def test_the_inventory_describes_every_published_action() -> None:
    """Every action a consumer can pin must appear in the inventory."""
    inventory = action_pins.load_inventory()
    published = set(action_pins.first_party_actions())
    described = set(inventory["actions"])

    assert published <= described, (
        f"published actions missing from the inventory: {sorted(published - described)}"
    )


def test_the_inventory_records_the_retirement_list() -> None:
    """The shipped inventory must carry the same retirements as the module."""
    inventory = action_pins.load_inventory()

    assert inventory["retired"] == RETIRED_REVISIONS


def test_inventory_lookups_are_anchored_on_real_action_paths() -> None:
    """Each recorded action must name a manifest that exists.

    An inventory keyed by a path that no longer exists would answer lookups
    for a pin nobody can write, while staying silent about the real ones.
    """
    inventory = action_pins.load_inventory()
    missing = [
        f"{action}: {record['manifest']}"
        for action, record in inventory["actions"].items()
        if not (REPO_ROOT / record["manifest"]).is_file()
        or reference_path(action) != record["manifest"]
    ]

    assert not missing, f"inventory records name absent manifests: {missing}"


def test_an_unknown_reference_is_reported_rather_than_cleared() -> None:
    """A pin the inventory cannot describe must not read as safe.

    The failure this guards against is a check that passes because it looked
    nothing up: no retired dependency *found* is not the same as none present.
    """
    inventory = action_pins.load_inventory()
    unknown = f"{action_pins.OWNER}/.github/actions/not-published@{'0' * 40}"

    assert unknown_references(inventory, [unknown]) == (unknown,)
    assert reaches_retired(inventory, unknown) == ()


def test_a_reference_the_inventory_describes_is_not_reported_unknown() -> None:
    """The complement of the previous test, so the lookup is not vacuous."""
    inventory = action_pins.load_inventory()
    known = min(inventory["actions"])

    assert unknown_references(inventory, [known]) == ()


def test_the_inventory_is_valid_json_with_the_expected_sections() -> None:
    """The file a consumer parses must have the shape this module documents."""
    document = json.loads(action_pins.INVENTORY_PATH.read_text(encoding="utf-8"))
    required: typ.Final = {"schema", "retired", "actions", "revisions"}

    assert required <= set(document), (
        f"inventory is missing sections: {sorted(required - set(document))}"
    )
    assert document["schema"] == action_pins.INVENTORY_SCHEMA
