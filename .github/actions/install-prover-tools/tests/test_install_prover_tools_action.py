"""Contract for the action manifest: pins, digests, and the cache write policy."""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[1] / "action.yml"
PINNED = re.compile(r"^[^@]+@[0-9a-f]{40}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def manifest() -> dict[str, typ.Any]:
    """Parse the action manifest once."""
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _steps(manifest: dict[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return the composite steps."""
    return manifest["runs"]["steps"]


def test_the_default_pin_is_a_full_sha(manifest: dict[str, typ.Any]) -> None:
    """The estate revision is immutable by construction."""
    default = manifest["inputs"]["prover-tools-sha"]["default"]
    assert FULL_SHA.fullmatch(default), default


def test_every_third_party_step_is_pinned_by_sha(manifest: dict[str, typ.Any]) -> None:
    """No moving tag reaches a consumer's job through this action."""
    uses = [step["uses"] for step in _steps(manifest) if "uses" in step]
    assert uses, "the action reaches cache and uv actions"
    assert all(PINNED.fullmatch(entry) for entry in uses), uses


def test_the_action_restates_no_digest(manifest: dict[str, typ.Any]) -> None:
    """Digests belong to the pinned rust-prover-tools revision, not this file."""
    text = ACTION.read_text(encoding="utf-8")
    long_hex = set(re.findall(r"\b[0-9a-f]{64}\b", text))
    assert not long_hex, "a SHA-256 here would drift from the tool's own pins"


def test_saves_happen_only_on_the_default_branch_after_a_miss(
    manifest: dict[str, typ.Any],
) -> None:
    """A pull request never writes a key, and a hit is never overwritten."""
    saves = [
        s
        for s in _steps(manifest)
        if str(s.get("uses", "")).startswith("actions/cache/save@")
    ]
    assert len(saves) == 2, "one save per cached archive"
    for step in saves:
        condition = " ".join(str(step["if"]).split())
        assert "cache-hit != 'true'" in condition, step["name"]
        assert "github.event_name == 'push'" in condition, step["name"]
        assert "default_branch" in condition, step["name"]


def test_every_restore_has_a_matching_save_key(manifest: dict[str, typ.Any]) -> None:
    """The key a restore reads is the key its save writes."""
    keys: dict[str, list[str]] = {"restore": [], "save": []}
    for step in _steps(manifest):
        name = str(step.get("uses", "")).split("@")[0]
        for kind in keys:
            if name == f"actions/cache/{kind}":
                keys[kind].append(step["with"]["key"])
    assert sorted(keys["restore"]) == sorted(keys["save"]), keys


def test_installs_run_through_the_pinned_source(manifest: dict[str, typ.Any]) -> None:
    """The installer is `uv tool run --from <pinned source>`, never a local script."""
    install = next(s for s in _steps(manifest) if s.get("id") == "install")
    assert 'uv tool run --from "$SOURCE" prover-tools' in install["run"]
    assert install["env"]["SOURCE"] == "${{ steps.plan.outputs.source }}"


def test_the_verus_version_is_checked_after_install(
    manifest: dict[str, typ.Any],
) -> None:
    """The tool reuses an installed binary, so the action checks the version."""
    verify = next(s for s in _steps(manifest) if s.get("id") == "verify")
    assert "VERUS_VERSION" in verify["env"], "the pinned version reaches the step"
    assert '*"$VERUS_VERSION"*' in verify["run"], "the reported version is compared"
