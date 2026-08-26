# ADR 0003: Four-tier Python linting architecture

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

Skylos scans only the production modules selected by
`SKYLOS_PRODUCTION_TARGETS` and excludes `SKYLOS_EXCLUDE_FOLDERS`. The gate is
strict and runs in CI through the same `make lint` target. Skylos is provisioned
through its standalone Python 3.14 tool environment because it parses source
with its own runtime AST; the pin prevents phantom findings when current source
uses syntax an older runtime cannot parse.

Scan-only global options remain in `SKYLOS`. `SKYLOS_CLI` stays command-only so
`skylos-allow` can dispatch `skylos whitelist <symbol> --reason <reason>` in
the required order.

## Exceptions

Investigate every finding and remove genuine dead code. Prefer a typed
`[tool.skylos.dead_code]` entry-point rule for a framework callback, protocol
implementation, or other implicit runtime caller. Use a documented whitelist
entry only when that typed boundary cannot model a verified false positive.
`SYMBOL` and `REASON` must both contain non-whitespace text; `SYMBOL` avoids
the WSL-provided hostname `NAME` variable.

## Consequences

Contributors receive a blocking production dead-code check from the existing
lint command, while tests cannot keep production code artificially live. The
contract suite depends on a pinned Makeutil parser, so local developers and
each isolated CI full-suite job install the documented Rust toolchain and
revision before running pytest.

## References

- [Developer guide](../developers-guide.md#python-linting)
- [Skylos support hardening](https://github.com/leynos/cuprum/pull/307)
