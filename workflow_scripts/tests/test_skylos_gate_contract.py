"""Contract for the Skylos dead-code gate itself.

The recipe in the Makefile and the configuration in ``pyproject.toml``
decide what the gate scans and how strictly it fails. These read both
through Makeutil and ``tomllib`` so the reviewed scope and the strict
mode cannot drift without a failure here.
"""

from __future__ import annotations

import tomllib

from workflow_scripts.tests.skylos_contract_support import (
    _EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES,
    _EXPECTED_SKYLOS_WHITELIST_NAMES,
    _REPOSITORY_ROOT,
    _SKYLOS_CLI_TOKENS,
    _SKYLOS_EXCLUDE_TOKENS,
    _SKYLOS_LINT_TOKENS,
    _SKYLOS_PRODUCTION_TARGET_TOKENS,
    _SKYLOS_SCAN_TOKENS,
    _SKYLOS_VERSION_TOKENS,
    _mapping,
    _objects,
    _recipe_tokens,
    _sole_recipe_rule,
    _text_sequence,
    _variable_tokens,
)


class TestSkylosGateContract:
    """The lint target must scan the reviewed set in strict mode."""

    def test_skylos_lint_contract_uses_python_314_and_production_scope(
        self,
    ) -> None:
        """The lint target must run Skylos strictly against production modules only."""
        test_prerequisites = _text_sequence(
            _sole_recipe_rule("test").get("prerequisites"),
            subject="test target prerequisites",
        )
        assert "makeutil" in test_prerequisites, (
            "make test must require makeutil before contract tests execute"
        )
        assert _variable_tokens("SKYLOS_VERSION") == _SKYLOS_VERSION_TOKENS, (
            "Skylos version must remain pinned to 4.33.2"
        )
        assert _variable_tokens("SKYLOS_CLI") == _SKYLOS_CLI_TOKENS, (
            "Skylos CLI must use Python 3.14 before its pinned tool source"
        )
        assert _variable_tokens("SKYLOS") == _SKYLOS_SCAN_TOKENS, (
            "Skylos scan options must remain separate from the command-only CLI"
        )
        assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == (
            _SKYLOS_PRODUCTION_TARGET_TOKENS
        ), "Skylos must scan only the reviewed production module set"
        assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == _SKYLOS_EXCLUDE_TOKENS, (
            "Skylos must exclude test-only callers from its production scan"
        )
        commands = [
            command
            for command in _recipe_tokens("lint")
            if command[:1] == ("$(SKYLOS)",)
        ]
        assert commands == [_SKYLOS_LINT_TOKENS], (
            "make lint must run the strict production dead-code gate exactly once"
        )

    def test_skylos_configuration_is_strict_and_documents_every_exception(
        self,
    ) -> None:
        """The Skylos configuration must keep strict mode and reasons aligned."""
        with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
            configuration = tomllib.load(configuration_file)
        tool = _mapping(configuration.get("tool"), subject="tool configuration")
        skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
        gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
        assert gate.get("strict") is True, "Skylos strict gate mode must remain enabled"
        whitelist = _mapping(skylos.get("whitelist"), subject="Skylos whitelist")
        names = frozenset(
            _text_sequence(whitelist.get("names"), subject="whitelist names")
        )
        documented = _mapping(
            whitelist.get("documented"), subject="documented whitelist reasons"
        )
        assert names == _EXPECTED_SKYLOS_WHITELIST_NAMES, (
            "Skylos whitelist names must preserve the consciously reviewed set"
        )
        documented_names = frozenset(documented)
        assert documented_names == _EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES, (
            "Skylos documented whitelist names must preserve the reviewed set"
        )
        assert names <= documented_names, (
            "every legacy Skylos whitelist name must have a documented reason"
        )
        assert all(
            isinstance(reason, str) and reason.strip() for reason in documented.values()
        ), "every Skylos allow-list reason must contain verified runtime-caller text"
        dead_code = _mapping(
            skylos.get("dead_code", {}), subject="Skylos dead-code configuration"
        )
        entrypoints = _objects(
            dead_code.get("entrypoints", []), subject="Skylos dead-code entry points"
        )
        assert len(entrypoints) == 1, (
            "Skylos must retain exactly one reviewed entry-point rule"
        )
        entrypoint = entrypoints[0]
        assert set(entrypoint) == {"type", "name", "path", "reason"}, (
            "Skylos import entry point must keep the exact reviewed selector shape"
        )
        assert entrypoint["type"] == "import", "the rule must match imports only"
        assert _text_sequence(
            entrypoint["name"], subject="mutation runner import names"
        ) == ("MutantsInvocation", "build_arguments", "cargo_command"), (
            "Skylos import names must preserve the independently reviewed set"
        )
        assert entrypoint["path"] == "workflow_scripts/mutation_run_cargo.py", (
            "Skylos import entry point must remain confined to the mutation runner"
        )
        reason = entrypoint["reason"]
        assert isinstance(reason, str), "the import entry-point reason must be text"
        assert reason.strip(), (
            "the import entry point must document its verified runtime callers"
        )
