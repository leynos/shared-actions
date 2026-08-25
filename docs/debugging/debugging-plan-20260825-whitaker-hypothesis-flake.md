# Debugging Plan: Whitaker property-test flakiness

**Generated**: 2026-08-25
**Issue ID**: Rebase gate failure
**Severity**: Medium
**Falsification sub-agent**: alchemist
**Planning agent boundary**: This document was prepared by the planning agent.
Falsification must be executed by the named sub-agent, not by the planning
agent.

## Problem Statement

`make test` failed after the rebase because two Hypothesis properties for the
new `install-whitaker` action exceeded Hypothesis's default 200 ms deadline on
their first invocation, then passed when replayed. The suite must remain a
reliable gate without weakening its installation assertions.

## Context Summary

| Aspect | Details |
| --- | --- |
| First observed | 2026-08-25 rebase validation |
| Reproduction rate | Two first-run deadline failures in one parallel suite run |
| Affected components | `.github/actions/install-whitaker/tests/test_install_whitaker.py` |
| Recent changes | The rebased `origin/main` added the `install-whitaker` action |

### Error Artefacts

```plaintext
Hypothesis FlakyFailure: first execution took 399.81 ms, exceeding the
200 ms deadline; replay took 33.81 ms and passed.
```

## Hypotheses

### H1: Process-startup latency exceeds a unit-test deadline

**Claim**: The property tests launch Bash subprocesses, so cold process or
filesystem startup occasionally exceeds Hypothesis's 200 ms deadline although
the asserted installation behaviour is deterministic.

**Plausibility**: High — the failure log records a slow first call and a fast
replay for the same generated input.

**Prediction**: Running one affected property without xdist will preserve its
functional assertions and avoid an intermittent `DeadlineExceeded` failure.

#### H1 Falsification Plan

| Step | Action | Expected Negative Result |
| --- | --- | --- |
| 1 | Run one reported property with `pytest -n 0` and its existing test environment. | A deterministic assertion failure disproves pure startup latency. |
| 2 | Repeat the same targeted test once. | A repeatable semantic failure disproves a deadline-only cause. |

**Tooling**: `uv run` with the dependencies from the `make test` command.

**Confidence on falsification**: High for distinguishing a behavioural defect
from a one-off deadline failure.

### H2: Parallel tests share mutable state

**Claim**: The properties collide through a shared environment variable,
filesystem path, or Hypothesis example database when xdist runs them together.

**Plausibility**: Medium — the failing tests are both subprocess-based, but
each scenario is expected to use `tmp_path`.

**Prediction**: If state is shared, the targeted properties will fail when run
together with xdist but not separately.

#### H2 Falsification Plan

| Step | Action | Expected Negative Result |
| --- | --- | --- |
| 1 | Inspect the two properties and their helper inputs for non-`tmp_path` state. | No shared mutable state weakens this hypothesis. |
| 2 | Run the two properties together under xdist after H1 is tested. | Passing together weakens this hypothesis. |

**Tooling**: Targeted `pytest` selection only; do not run repository gates.

**Confidence on falsification**: Medium, because scheduler timing can mask a
race.

## Recommended Execution Order

1. **H1** — the log already strongly identifies a deadline breach and the
targeted experiment is decisive.
2. **H2** — run only if H1 is not falsified.

## Termination Criteria

- **Root cause identified**: One hypothesis survives its falsification test
while the other is disproved.
- **Escalation trigger**: Both hypotheses are falsified; revise the plan with
new evidence.

## Notes for Executing Agent

Use the exact failing tests from
`.github/actions/install-whitaker/tests/test_install_whitaker.py`. Do not edit
files or run the full repository gate suite.
