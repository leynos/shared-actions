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


def test_the_kani_install_directory_is_always_explicit(
    manifest: dict[str, typ.Any],
) -> None:
    """An inherited KANI_INSTALL_DIR cannot move the install off the PATH entry."""
    install = next(s for s in _steps(manifest) if s.get("id") == "install")
    assert '--install-dir "$kani_bin"' in install["run"], "the flag is unconditional"
    assert "kani_args" not in install["run"], "no conditional flag list remains"


def test_the_kani_install_directory_is_made_absolute(
    manifest: dict[str, typ.Any],
) -> None:
    """A relative CARGO_HOME cannot split the install from the PATH entry."""
    install = next(s for s in _steps(manifest) if s.get("id") == "install")
    assert 'kani_bin="$PWD/$kani_bin"' in install["run"], "relative paths are anchored"


def test_ambient_tool_defaults_are_unset_before_the_tool_runs(
    manifest: dict[str, typ.Any],
) -> None:
    """A caller's INPUT_* or *_INSTALL_DIR cannot steer the tool off the plan."""
    run = next(s for s in _steps(manifest) if s.get("id") == "install")["run"]
    assert run.index("unset") < run.index("prover()"), "scrubbed before first use"
    for name in ("KANI_INSTALL_DIR", "VERUS_INSTALL_DIR", "Z3_CACHE_DIR", "INPUT_"):
        assert name in run, name


def test_a_save_needs_an_archive_this_run_published(
    manifest: dict[str, typ.Any],
) -> None:
    """An empty or reused install never writes a cache entry."""
    saves = [
        s
        for s in _steps(manifest)
        if str(s.get("uses", "")).startswith("actions/cache/save@")
    ]
    for step in saves:
        assert "-archive == 'true'" in " ".join(str(step["if"]).split()), step["name"]


def test_saves_follow_a_digest_check_of_the_archives(
    manifest: dict[str, typ.Any],
) -> None:
    """The tool re-verifies archives on a miss, and a save needs that to succeed."""
    steps = _steps(manifest)
    names = [s.get("id") for s in steps]
    presave = steps[names.index("presave")]
    assert "--no-setup --no-verify" in presave["run"], "kani verifies without setup"
    saves = [
        s for s in steps if str(s.get("uses", "")).startswith("actions/cache/save@")
    ]
    assert all(steps.index(s) > steps.index(presave) for s in saves), (
        "check comes first"
    )
    for step in saves:
        assert "presave.outcome == 'success'" in " ".join(str(step["if"]).split())
