"""End-to-end cases for the configuration, the API and the command line.

These write the compliant fixture tree to disk and run the public surface
over it, so the reading boundary, the clause naming and the exit statuses
are proved together, as a consumer's Makefile target meets them.
"""

from __future__ import annotations

import typing as typ

import pytest
from contract_fixtures import tree
from cv005_contracts import (
    Config,
    ConfigError,
    ContractError,
    assert_environment_contract,
    assert_publisher_contract,
    load_config,
    violations,
)
from cv005_contracts.cli import EXIT_CLEAN, EXIT_UNREADABLE, EXIT_VIOLATIONS, check
from cv005_contracts.config import config_from_mapping

if typ.TYPE_CHECKING:
    from pathlib import Path

#: The compliant tree's parameters, as a consumer would write them.
CONFIG_TEXT: typ.Final[str] = 'repository = "leynos/example"\ninterpreter = "3.13"\n'


def _write_tree(root: Path, texts: dict[str, str], config: str = CONFIG_TEXT) -> Path:
    """Write a fixture tree and its configuration under a repository root."""
    for name, text in texts.items():
        path = (
            root / name / "action.yml"
            if "/" in name
            else root / ".github" / "workflows" / name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / ".github" / "cv005.toml").write_text(config, encoding="utf-8")
    return root


def test_the_compliant_tree_passes_every_family(tmp_path: Path) -> None:
    """Every clause holds on the fixture tree read from disk."""
    root = _write_tree(tmp_path, tree())
    found = violations(root)
    assert found == [], found


def test_a_breach_names_its_clause(tmp_path: Path) -> None:
    """A cancelling publisher is reported under the concurrency clause."""
    texts = tree()
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        "cancel-in-progress: false", "cancel-in-progress: true"
    )
    root = _write_tree(tmp_path, texts)
    clauses = {item.clause for item in violations(root)}
    assert clauses == {"publisher.concurrency"}, clauses


def test_the_environment_family_is_separate(tmp_path: Path) -> None:
    """Dropping the environment breaks only the environment contract."""
    texts = tree()
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        "    environment: codescene\n", ""
    )
    root = _write_tree(tmp_path, texts)
    assert_publisher_contract(root)
    with pytest.raises(ContractError) as raised:
        assert_environment_contract(root)
    clauses = {item.clause for item in raised.value.violations}
    assert clauses == {"environment.placement"}, clauses


def test_a_configured_selection_is_held(tmp_path: Path) -> None:
    """A generator input differing from the configured selection is refused."""
    config = CONFIG_TEXT + '[selection]\nformat = "lcov"\n'
    root = _write_tree(tmp_path, tree(), config)
    clauses = [item.clause for item in violations(root)]
    assert clauses == ["coverage.selection"], clauses


def test_the_publisher_must_carry_its_configured_name(tmp_path: Path) -> None:
    """A publisher filed under another name is reported."""
    config = CONFIG_TEXT + 'publisher = "publish.yml"\n'
    root = _write_tree(tmp_path, tree(), config)
    clauses = [item.clause for item in violations(root)]
    assert clauses == ["publisher.name"], clauses


def test_the_interpreter_clause_runs_only_when_configured(tmp_path: Path) -> None:
    """A repository measuring no Python sets no interpreter and is not held."""
    texts = {
        name: text.replace("        env:\n          UV_PYTHON: '3.13'\n", "")
        for name, text in tree().items()
    }
    unpinned = _write_tree(
        tmp_path / "unpinned", texts, 'repository = "leynos/example"\n'
    )
    assert violations(unpinned) == []
    pinned = _write_tree(tmp_path / "pinned", texts)
    clauses = {item.clause for item in violations(pinned)}
    assert clauses == {"coverage.interpreter"}, clauses


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({}, "repository"),
        ({"repository": "example"}, "repository"),
        ({"repository": "leynos/example", "publihser": "x.yml"}, "unknown keys"),
        ({"repository": "leynos/example", "environment": "yes"}, "bool"),
        ({"repository": "leynos/example", "interpreter": 3.13}, "str"),
        ({"repository": "leynos/example", "selection": {"format": 1}}, "strings"),
    ],
)
def test_a_malformed_configuration_is_refused(
    raw: dict[str, object], fragment: str
) -> None:
    """A misspelt or mistyped parameter never falls back to a default."""
    with pytest.raises(ConfigError, match=fragment):
        config_from_mapping(raw)


def test_a_missing_configuration_is_refused(tmp_path: Path) -> None:
    """A repository with no parameters file cannot be checked."""
    with pytest.raises(ConfigError, match="could not be read"):
        load_config(tmp_path)


def test_defaults_fill_the_optional_keys() -> None:
    """Only the repository is required."""
    config = config_from_mapping({"repository": "leynos/example"})
    assert config == Config(repository="leynos/example"), config


def test_an_unknown_family_is_refused(tmp_path: Path) -> None:
    """A misspelt family must not run nothing and pass."""
    root = _write_tree(tmp_path, tree())
    with pytest.raises(ValueError, match="unknown contract families"):
        violations(root, only=frozenset({"publsher"}))


def test_the_cli_exits_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A compliant tree exits 0 and prints nothing."""
    root = _write_tree(tmp_path, tree())
    assert check(repository=root) == EXIT_CLEAN
    assert capsys.readouterr().out == ""


def test_the_cli_reports_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A breach exits 1 with one line naming the clause."""
    texts = tree()
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  schedule:\n    - cron: '0 0 * * *'\n",
    )
    root = _write_tree(tmp_path, texts)
    assert check(repository=root) == EXIT_VIOLATIONS
    out = capsys.readouterr().out
    assert out.startswith("publisher.triggers: "), out


@pytest.mark.parametrize("breakage", ["no-config", "no-workflows", "duplicate-key"])
def test_the_cli_refuses_what_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], breakage: str
) -> None:
    """A reading failure exits 2, never 0."""
    texts = tree()
    if breakage == "duplicate-key":
        texts["ci.yml"] = texts["ci.yml"].replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    runs-on: ubuntu-latest\n",
        )
    root = _write_tree(tmp_path, texts)
    if breakage == "no-config":
        (root / ".github" / "cv005.toml").unlink()
    if breakage == "no-workflows":
        for path in (root / ".github" / "workflows").iterdir():
            path.unlink()
    assert check(repository=root) == EXIT_UNREADABLE
    assert "cv005-contracts:" in capsys.readouterr().err


def test_the_cli_runs_only_the_selected_family(tmp_path: Path) -> None:
    """`--only environment` ignores a publisher breach."""
    texts = tree()
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        "cancel-in-progress: false", "cancel-in-progress: true"
    )
    root = _write_tree(tmp_path, texts)
    assert check(repository=root, only=("environment",)) == EXIT_CLEAN
    assert check(repository=root, only=("publisher",)) == EXIT_VIOLATIONS
