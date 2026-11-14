# ADR 0007: Four-tier Python linting architecture

**Status:** Accepted **Date:** 2026-08-27

## Context

The repository combines Python action scripts, GitHub Action metadata, and a
Rust fixture. Ruff catches broad Python-source issues, action-validator checks
metadata, and Whitaker keeps the Rust fixture warning-free. None of those tools
proves that production Python symbols have live callers. Test-only imports can
otherwise conceal dead production code.

## Decision

`make lint` SHALL run four tiers in order:

1. Ruff for Python source and import hygiene.
2. action-validator for GitHub Action metadata.
3. Whitaker for the Rust fixture with warnings denied.
4. Skylos for strict production Python dead-code detection.

Skylos scans only the production modules selected by `[tool.df12_skylos]` and
excludes the paths in `[tool.skylos] exclude`. The gate is strict and runs in
CI through the same `make lint` target. Skylos is provisioned through the
installed `df12-skylos` command in a standalone Python 3.14 tool environment
because it parses source with its own runtime AST; the pin prevents phantom
findings when current source uses syntax an older runtime cannot parse.

Update, 2026-11-13: the earlier `SKYLOS_*` Makefile variables, the
cross-platform lock helper and the Makefile contract suites were replaced by the
`df12-skylos` command from df12-python-lints, whose behaviour is tested there.
The earlier notes describe the previous form: scan-only global options lived in
`SKYLOS`, and `SKYLOS_CLI` stayed command-only so `skylos-allow` could dispatch
`skylos whitelist <symbol> --reason <reason>` in the required order.

## Exceptions

Investigate every finding and remove genuine dead code. Prefer a typed
`[tool.skylos.dead_code]` entry-point rule for a framework callback, protocol
implementation, or other implicit runtime caller. Use a documented whitelist
entry only when that typed boundary cannot model a verified false positive.
`SYMBOL` and `REASON` must both contain non-whitespace text; `SYMBOL` avoids
the WSL-provided hostname `NAME` variable. The `skylos-allow` target hands the
values to `df12-skylos allow`, which takes the ignored repository-local
`.pyproject.toml.df12.lock` before changing the documented allow list, so
concurrent exception updates do not lose entries.

## Consequences

Contributors receive a blocking production dead-code check from the existing
lint command, while tests cannot keep production code artificially live. The
contract suite depends on a pinned Makeutil parser, so local developers and
each isolated CI full-suite job install the documented Rust toolchain and
revision before running pytest.

## References

- [Developer guide](../developers-guide.md#python-linting)
- [Skylos support hardening](https://github.com/leynos/cuprum/pull/307)
