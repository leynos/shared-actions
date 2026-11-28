"""Behaviour of the plan the action resolves before it downloads anything."""

from __future__ import annotations

import typing as typ

import prover_plan
import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

SHA = "592558f7183b554fc749696246984c5d7300d029"


def _request(root: Path, **overrides: str) -> prover_plan.Request:
    """Build a request over *root*, with *overrides* replacing the defaults."""
    values: dict[str, typ.Any] = {
        "tools": "kani verus",
        "sha": SHA,
        "repo_root": root,
        "runner_image": "ubuntu2404",
        "lane": "Linux-X64-Hosted",
        "cache_root": root / "cache",
    }
    values.update(overrides)
    return prover_plan.Request(**values)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a consumer repository holding both version pins."""
    for name, version in (("kani", "0.67.0"), ("verus", "0.2026.09.06.8dea4a2")):
        path = tmp_path / "tools" / name / "VERSION"
        path.parent.mkdir(parents=True)
        path.write_text(version + "\n", encoding="utf-8")
    return tmp_path


def test_verus_brings_z3_and_every_key_names_its_components(repo: Path) -> None:
    """Each tool's key carries lane, image, tool, version and the SHA prefix."""
    outputs = prover_plan.plan(_request(repo))

    assert outputs["tools"] == "kani verus z3", "verus implies z3"
    assert outputs["kani-key"] == (
        "prover-tools-v1-Linux-X64-Hosted-ubuntu2404-kani-0.67.0-592558f7183b"
    )
    assert outputs["verus-key"].endswith("-verus-0.2026.09.06.8dea4a2-592558f7183b")
    assert outputs["z3-key"].endswith("-z3-pinned-592558f7183b"), (
        "z3 has no version file, so the revision is its version"
    )
    assert outputs["source"] == f"git+https://github.com/leynos/rust-prover-tools@{SHA}"


def test_a_new_revision_changes_every_key(repo: Path) -> None:
    """A bumped pin cannot be satisfied by archives an earlier pin left."""
    other = "0" * 40
    first = prover_plan.plan(_request(repo))
    second = prover_plan.plan(_request(repo, sha=other))

    for tool in ("kani", "verus", "z3"):
        assert first[f"{tool}-key"] != second[f"{tool}-key"], tool


def test_a_new_tool_version_changes_its_key(repo: Path) -> None:
    """Raising a VERSION file moves that tool's key and no other."""
    before = prover_plan.plan(_request(repo))
    (repo / "tools" / "kani" / "VERSION").write_text("0.68.0\n", encoding="utf-8")
    after = prover_plan.plan(_request(repo))

    assert before["kani-key"] != after["kani-key"], "kani key follows its version"
    assert before["z3-key"] == after["z3-key"], "other keys are untouched"


def test_default_directories_sit_under_the_repository(repo: Path) -> None:
    """Verus and Z3 install beneath `.verus`, where consumers' Makefiles look."""
    outputs = prover_plan.plan(_request(repo))

    assert outputs["verus-install-dir"] == str(
        repo / ".verus" / "0.2026.09.06.8dea4a2"
    ), "verus defaults to its versioned directory"
    assert outputs["z3-install-dir"] == str(repo / ".verus" / "z3")


def test_only_requested_tools_are_planned(repo: Path) -> None:
    """Asking for Kani alone plans neither Verus nor Z3."""
    outputs = prover_plan.plan(_request(repo, tools="kani"))

    assert (outputs["kani"], outputs["verus"], outputs["z3"]) == (
        "true",
        "false",
        "false",
    ), "only kani is selected"
    assert "verus-key" not in outputs, "no verus key is rendered"


@pytest.mark.parametrize(
    "sha",
    ["main", "592558f", "592558F7183B554FC749696246984C5D7300D029", SHA + "0", ""],
)
def test_a_pin_that_is_not_a_full_sha_is_refused(repo: Path, sha: str) -> None:
    """Only a full lower-case commit SHA names an immutable installer."""
    with pytest.raises(prover_plan.PlanError, match="40-character"):
        prover_plan.plan(_request(repo, sha=sha))


@pytest.mark.parametrize("tools", ["", "kani cbmc", "Kani"])
def test_an_unknown_or_empty_tool_list_is_refused(repo: Path, tools: str) -> None:
    """The tool list names only what rust-prover-tools installs."""
    with pytest.raises(prover_plan.PlanError, match="tools must name"):
        prover_plan.plan(_request(repo, tools=tools))


def test_a_missing_version_file_is_named(repo: Path) -> None:
    """The error says which file is absent rather than failing obscurely."""
    (repo / "tools" / "verus" / "VERSION").unlink()

    with pytest.raises(prover_plan.PlanError, match=r"tools.verus.VERSION"):
        prover_plan.plan(_request(repo))


def test_a_version_that_could_escape_the_key_is_refused(repo: Path) -> None:
    """A version file cannot smuggle path or key syntax."""
    (repo / "tools" / "kani" / "VERSION").write_text("../x\n", encoding="utf-8")

    with pytest.raises(prover_plan.PlanError, match="does not hold a version"):
        prover_plan.plan(_request(repo))


def test_the_command_writes_github_output(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry point appends key=value lines to the runner's output file."""
    target = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))

    status = prover_plan.main(
        [
            "--tools", "z3", "--sha", SHA, "--repo-root", str(repo),
            "--runner-image", "ubuntu2404", "--lane", "Linux-X64-Hosted",
            "--cache-root", str(tmp_path / "c"),
        ]
    )  # fmt: skip

    assert status == 0, "a valid plan succeeds"
    assert "z3=true" in target.read_text(encoding="utf-8").splitlines()


def test_relative_install_directories_resolve_against_the_repository(
    repo: Path,
) -> None:
    """A relative directory is read as the tool reads it, from the repo root."""
    outputs = prover_plan.plan(
        _request(repo, z3_install_dir="out/z3", kani_install_dir="bin", tools="kani z3")
    )

    assert outputs["z3-install-dir"] == str(repo.resolve() / "out" / "z3")
    assert outputs["kani-install-dir"] == str(repo.resolve() / "bin")


def test_an_explicit_verus_directory_is_still_version_qualified(repo: Path) -> None:
    """A bumped pin lands in a new directory, so a stale binary is never reused."""
    outputs = prover_plan.plan(_request(repo, verus_install_dir="cache/verus"))

    assert outputs["verus-install-dir"] == str(
        repo.resolve() / "cache" / "verus" / "0.2026.09.06.8dea4a2"
    ), "the version names the final directory"


def test_cache_false_plans_no_cache_and_rejects_other_values(repo: Path) -> None:
    """`cache: false` switches both archive caches off; anything else is refused."""
    off = prover_plan.plan(_request(repo, tools="kani verus", cache="false"))
    on = prover_plan.plan(_request(repo, tools="kani verus"))

    assert (off["kani-cached"], off["z3-cached"]) == ("false", "false")
    assert (on["kani-cached"], on["z3-cached"]) == ("true", "true")
    with pytest.raises(prover_plan.PlanError, match="cache must be"):
        prover_plan.plan(_request(repo, cache="yes"))


PIN = """\
schema_version = 1
repository = "leynos/rust-prover-tools"
tag = "kani-snapshot-2026-11-0d2328a93f0e-r2"
upstream_commit = "0d2328a93f0e0ff66132d6bfa1a7d884877cf862"
builder_commit = "85421417cefd5eb642d671f71f4a6b17a9abb0e4"
target = "x86_64-unknown-linux-gnu"
manifest_sha256 = "c5e2fa595232f0a25e92f4f5ff9ab11f72a374600d713201c3a9e8a41b1b8a8f"
"""
SNAPSHOT_SHA = "f3ac307ef81b3af63f56c7e4a4191e566314e242"


def _write_pin(root: Path, text: str = PIN) -> Path:
    """Write a snapshot pin into the consumer and return its path."""
    pin = root / "tools" / "kani" / "snapshot.toml"
    pin.write_text(text, encoding="utf-8")
    return pin


def test_without_a_pin_the_plan_is_the_legacy_plan(repo: Path) -> None:
    """No pin leaves Kani on the release path, with its key and cache intact."""
    outputs = prover_plan.plan(_request(repo, tools="kani"))

    assert outputs["kani-snapshot"] == "false", "no snapshot without a pin"
    assert outputs["kani"] == "true", "the legacy install still runs"
    assert outputs["kani-cached"] == "true", "the legacy archive cache is kept"
    assert outputs["kani-version"] == "0.67.0", "the version file still decides"


def test_a_pin_routes_kani_to_the_snapshot_and_off_the_legacy_install(
    repo: Path,
) -> None:
    """A pin replaces the legacy Kani install and needs no VERSION file."""
    pin = _write_pin(repo)
    (repo / "tools" / "kani" / "VERSION").unlink()

    outputs = prover_plan.plan(_request(repo, tools="kani", sha=SNAPSHOT_SHA))

    assert outputs["kani-snapshot"] == "true", "the snapshot is selected"
    assert outputs["kani-snapshot-pin"] == str(pin.resolve()), "the pin is named"
    assert (outputs["kani"], outputs["kani-cached"]) == ("false", "false")
    assert "kani-key" not in outputs, "no legacy archive key is rendered"


def test_a_pin_leaves_the_other_tools_alone(repo: Path) -> None:
    """Verus and Z3 still install and cache while Kani uses the snapshot."""
    _write_pin(repo)

    outputs = prover_plan.plan(_request(repo, sha=SNAPSHOT_SHA))

    assert outputs["tools"] == "verus z3", "kani leaves the legacy list"
    assert outputs["kani-snapshot"] == "true", "kani is still installed"
    assert outputs["z3-cached"] == "true", "z3 keeps its cache"


def test_kani_snapshot_false_ignores_a_pin(repo: Path) -> None:
    """`false` keeps the legacy install even where a pin exists."""
    _write_pin(repo)

    outputs = prover_plan.plan(_request(repo, tools="kani", kani_snapshot="false"))

    assert (outputs["kani-snapshot"], outputs["kani"]) == ("false", "true")


def test_a_pin_without_kani_requested_plans_no_snapshot(repo: Path) -> None:
    """A pin does nothing for a job that installs only Z3."""
    _write_pin(repo)

    outputs = prover_plan.plan(_request(repo, tools="z3"))

    assert outputs["kani-snapshot"] == "false", "kani was not asked for"


def test_kani_snapshot_true_without_a_pin_is_named(repo: Path) -> None:
    """Requiring a snapshot where none is pinned fails naming the file."""
    with pytest.raises(prover_plan.PlanError, match=r"snapshot\.toml"):
        prover_plan.plan(
            _request(repo, tools="kani", sha=SNAPSHOT_SHA, kani_snapshot="true")
        )


def test_kani_snapshot_true_without_kani_is_refused(repo: Path) -> None:
    """The requirement cannot be met by a job that installs no Kani."""
    with pytest.raises(prover_plan.PlanError, match="kani is not in tools"):
        prover_plan.plan(_request(repo, tools="z3", kani_snapshot="true"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not = [valid", "not valid TOML"),
        ("", "missing"),
        (PIN + 'extra = "x"\n', "unknown extra"),
        (PIN.replace("schema_version = 1\n", ""), "missing schema_version"),
    ],
)
def test_a_malformed_pin_is_named(repo: Path, text: str, message: str) -> None:
    """A pin that cannot be a version 1 document fails with the file named."""
    _write_pin(repo, text)

    with pytest.raises(prover_plan.PlanError, match=message) as raised:
        prover_plan.plan(_request(repo, tools="kani", sha=SNAPSHOT_SHA))
    assert "snapshot.toml" in str(raised.value), "the file is named"


def test_a_pin_with_a_revision_that_predates_snapshots_is_refused(repo: Path) -> None:
    """The default revision has no `kani snapshot`, so the plan says so."""
    _write_pin(repo)

    with pytest.raises(prover_plan.PlanError, match="predates it"):
        prover_plan.plan(_request(repo, tools="kani"))


def test_an_unknown_kani_snapshot_value_is_refused(repo: Path) -> None:
    """Only auto, true and false are inputs."""
    with pytest.raises(prover_plan.PlanError, match="kani-snapshot must be"):
        prover_plan.plan(_request(repo, kani_snapshot="yes"))
