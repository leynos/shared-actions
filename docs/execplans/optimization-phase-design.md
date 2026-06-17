# Benchmarking and Optimization Phase Design

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: APPROVED

## Purpose / big picture

This work establishes femtologging's benchmarking and performance optimization
strategy as a first-class roadmap phase. After completion, the team will have:

1. A comprehensive roadmap document detailing the benchmarking and
   optimization phases.
2. A technical design document specifying fair comparison methodology,
   benchmark architecture, and optimization guidance.
3. A foundation for measurable performance improvements guided by evidence
   rather than intuition.

The user-visible outcome is a credible public comparison suite showing
femtologging's caller-latency advantage against stdlib `logging`,
`picologging`, and `loguru`, paired with an internal laboratory for guiding
optimization work toward the highest-impact bottlenecks.

## Constraints

- All benchmarking must enforce architectural parity: queue-based and
  synchronous handler patterns must be compared separately, never conflated
  in a single bar chart.
- The design must support Python 3.12+; comparisons run only on supported
  CPython versions.
- Benchmarking code must use pyperf for Python cases (calibration, multiple
  workers, instability detection, JSON output) and Criterion for Rust
  internals.
- The roadmap document must live in `docs/roadmap.md` and use the
  roadmap-doc skill's style.
- The tech design document must live in `docs/tech/benchmarking-and-
  optimization.md` and use tech-design-doc skill's style.
- All text must follow British English (en-gb-oxendict) conventions,
  including Oxford comma application only when it improves clarity.
- The branch must be named `optimization-phase-design`, tracking
  `origin/optimization-phase-design`.
- A PR must be created using the pr-creation skill before this task is
  considered complete.

## Tolerances (exception triggers)

- Scope: if the roadmap or design documents exceed 2000 lines of
  substantive content (combined), stop and escalate to prioritise.
- Dependencies: if external libraries beyond pyperf and Criterion are
  required for v0 benchmarking, stop and escalate.
- Ambiguity: if the distinction between "public suite" and "internal lab"
  benchmarks becomes unclear during drafting, stop and present
  clarifications.
- Research gaps: if key information on loguru's `enqueue=True` drain
  behaviour or picologging queue modes cannot be confirmed via firecrawl,
  note the gap and proceed with documented assumptions.

## Risks

- Risk: loguru's `enqueue=True` drain semantics may differ from documented
  behaviour when the logger is destroyed or `complete()` is called.
  Severity: medium
  Likelihood: low
  Mitigation: The design document will call this out as a verification point
  for the v0 benchmark; initial runs will probe exact semantics.

- Risk: picologging queue mode may not exist or may have incompatible API
  relative to stdlib QueueHandler.
  Severity: medium
  Likelihood: medium
  Mitigation: Design includes picologging queue mode as "optional if
  available"; v0 starts with sync only and adds queue mode post-verification.

- Risk: The three-leaderboard model (caller latency, end-to-end, diagnostic
  internals) may be perceived as overcomplicated by external users.
  Severity: low
  Likelihood: medium
  Mitigation: The design document includes a "benchmark meaning" primer
  explaining why three leaderboards prevent misleading comparisons.

- Risk: Rust internal benchmarks (Criterion) may show noise on the shared CI
  runner, making regressions hard to gate.
  Severity: medium
  Likelihood: high
  Mitigation: The design recommends running Criterion on pinned bare-metal
  or mostly-idle hardware, not shared CI; CI runs a smoke variant.

## Progress

- [x] (2026-06-17 14:00Z) Research phase: gathered information on pyperf,
  picologging, loguru enqueue, and Python QueueHandler/QueueListener patterns
  via firecrawl.
- [x] Write execution plan (this document).
- [x] Create roadmap document using roadmap-doc skill covering benchmarking
  and optimization phases with milestones.
- [x] Create technical design document using tech-design-doc skill detailing
  benchmark strategy, fairness rules, and optimization playbook.
- [x] Apply en-gb-oxendict style corrections across both documents.
- [x] Switch to `optimization-phase-design` branch and track
  `origin/optimization-phase-design`.
- [x] Create pull request using pr-creation skill with lody session reference.
- [ ] Fix markdown lint failures (line length, table alignment, code blocks).

## Surprises & discoveries

None yet; research phase confirmed expected information.

## Decision log

- Decision: Structure the design around three separate leaderboards (caller latency, end-to-end, diagnostic internals) rather than a single unified benchmark.
  Rationale: A single leaderboard conflates architecture (femtologging's queue model vs stdlib direct handlers) with implementation quality, leading to misleading comparisons. The three-leaderboard model isolates each concern and makes fairness explicit.
  Date/Author: 2026-06-17 Claude (based on user specification).

- Decision: Recommend pyperf for Python and Criterion for Rust rather than ad hoc timing loops or pytest-benchmark.
  Rationale: pyperf provides automatic calibration, multiple worker processes, instability detection, JSON output, and significance testing—all critical for reliable benchmarks. Criterion already in use in the repo (config.rs); Rust internals should stay consistent.
  Date/Author: 2026-06-17 Claude.

- Decision: Benchmarking v0 will start narrow (disabled logs, enabled null handler, single-threaded file, configured benchmarks) and expand post-validation.
  Rationale: A narrow v0 suite reduces risk of architectural errors in the adapter layer and measurement protocols before expanding to full matrix.
  Date/Author: 2026-06-17 Claude.

## Outcomes & retrospective

Work in progress; this section will be completed upon finishing the roadmap and
design documents.

## Context and orientation

Femtologging is a high-performance async Python logging library built on
Rust with an MPSC queue architecture. Log records created on producer threads
are enqueued and processed by dedicated consumer threads (Rust-based workers)
that handle formatting and I/O. The library aims to minimise caller-side
latency whilst providing predictable end-to-end logging behaviour.

The user has provided extensive specifications for benchmarking strategy,
including:

- Architecture-aware comparison (queue vs. synchronous direct handlers
  compared separately).
- Three distinct leaderboards (caller latency, end-to-end completion,
  diagnostic internals).
- Specific comparison targets (stdlib logging, stdlib
  QueueHandler/QueueListener, picologging, loguru with enqueue=True,
  femtologging variants).
- Benchmark dimensions (framework, handler, workload, concurrency,
  queue/batch settings).
- Metrics to collect (caller_ns_per_call, records_enqueued/drained_per_
  second, end-to-end latencies, flush time, memory, allocations, context
  switches, syscalls).
- Repository structure for benchmarks (adapters, cases, sinks, runner,
  schema, compare, report).
- Fairness rules (equivalent semantics, idiomatic vs. parity modes,
  disabled-log handling).
- Measurement protocols (warm-up, pyperf, correctness verification, JSON
  export, markdown tables).
- Concrete benchmark groups (disabled hot path, enabled null, direct I/O vs.
  queued, file batching, socket, saturation, configuration).
- Regression policy with thresholds by category.
- Optimisation playbook keyed to failing benchmarks.
- Strategic success criteria (must win: caller latency with I/O,
  multi-threaded throughput, predictable drain, low/zero drops; nice to win:
  single-threaded null, config speed; do not contort: eager f-strings,
  terminal output, per-record flush).

Key files referenced:

- Existing: `rust_extension/benches/config.rs` (Criterion configuration
  benchmarks).
- New: `benchmarks/` directory with Python benchmarking suite.
- Documents: `docs/roadmap.md` (roadmap),
  `docs/tech/benchmarking-and-optimization.md` (design).

## Plan of work

### Stage A: Create roadmap document

The roadmap document serves as a high-level strategic view of how benchmarking
and optimization fit into femtologging's evolution. It will:

1. Position benchmarking and optimization as a distinct roadmap phase.
2. List key milestones: v0 smoke suite, full Python matrix, Rust internal lab,
regression gates, public reporting.
3. Explain the three-leaderboard philosophy and why it prevents benchmark
misinterpretation.
4. Outline success criteria and measurement strategy.
5. Reference the companion technical design document for implementation details.

Use the roadmap-doc skill to author `docs/roadmap.md` in standard structure
(goals, phases, milestones, dependencies, risks, success metrics).

### Stage B: Create technical design document

The design document is the executable specification. It will:

1. Deep-dive into benchmark philosophy and the three-leaderboard model.
2. Specify comparison targets, benchmark dimensions, and metric schema.
3. Outline repository structure for the benchmarking code.
4. Document fairness rules (architectural parity, idiomatic vs. parity modes,
disabled-log handling).
5. Provide measurement protocols (warm-up, pyperf, correctness verification,
JSON/markdown export).
6. Detail concrete benchmark groups (disabled hot path, enabled null, I/O,
batching, socket, saturation, configuration).
7. Include the optimization playbook: bottleneck classification and targeted
fixes.
8. Specify regression policy (thresholds by category, rolling baseline).
9. Document reporting format (raw JSON, Criterion output, summary tables).
10. Provide an immediate v0 implementation plan.

Use the tech-design-doc skill to author
`docs/tech/benchmarking-and-optimization.md` in standard structure (overview,
philosophy, design details, acceptance criteria, appendices).

### Stage C: Apply British English and Oxford dictionary style

Use the en-gb-oxendict skill to scan both documents and apply:

- British spelling (optimise, realise, etc.).
- Oxford comma only where clarity improves (default to no comma in series unless it prevents ambiguity).
- Consistent terminology (e.g., "flume" vs. "femtologging"—should be "femtologging").
- Formal tone and register suitable for technical design documents.

### Stage D: Branch switch and PR creation

1. Ensure all uncommitted changes are staged (roadmap and design documents).
2. Switch to or create branch `optimization-phase-design` tracking
`origin/optimization-phase-design`.
3. Commit changes with a meaningful commit message.
4. Use pr-creation skill to create a pull request with:
   - Title: "Benchmarking and Optimization Phase Design"
   - Body: Summary of roadmap and design, key decisions, strategic success criteria.
   - Reference: Lody session link.
5. Ensure the PR passes Makefile gates (lint, format check).

## Concrete steps

### Roadmap document

1. Create or update `docs/roadmap.md`.
2. Use roadmap-doc skill with the following outline:
   - Phase 1: Foundation (v0 smoke suite, adapter layer, measurement protocols).
   - Phase 2: Expansion (full Python matrix, multi-threaded concurrency sweep, Rust internals).
   - Phase 3: Regression gates and public reporting (establish baselines, CI gates, public comparison tools).
   - Phase 4: Optimization loop (target high-impact bottlenecks, implement, measure, regression gate).
3. Include milestones, dependencies, and success criteria specific to
benchmarking.

### Technical design document

1. Create or update `docs/tech/benchmarking-and-optimization.md`.
2. Use tech-design-doc skill with sections:
   - Benchmark Philosophy (three leaderboards, why they matter).
   - Comparison Targets (table of frameworks, modes, rationale).
   - Benchmark Dimensions (framework, handler, workload, concurrency, settings).
   - Metrics Schema (caller latency, throughput, E2E, memory, drops, etc.).
   - Repository Structure (benchmarks/, adapters/, cases/, sinks/, runner, schema, compare, report).
   - Fairness Rules (architectural parity, idiomatic vs. parity, disabled-log handling).
   - Measurement Protocols (warm-up, pyperf, correctness, JSON/markdown export).
   - Concrete Benchmark Groups (disabled hot path, enabled null, I/O, batching, socket, saturation, configuration).
   - Optimization Playbook (bottleneck classification and targeted fixes).
   - Regression Policy (thresholds, rolling baseline, CI gates).
   - Reporting Format (JSON, Criterion, summary tables).
   - V0 Implementation Plan (narrow scope, phased expansion).
3. Include concrete code examples for adapter interface and metrics schema.

### British English correction

1. Run en-gb-oxendict skill on both `docs/roadmap.md` and
`docs/tech/benchmarking-and-optimization.md`.
2. Review changes and accept or customize as needed.

### Branch and PR

1. Verify current branch via `git branch --show-current`.
2. If not on `optimization-phase-design`, create and switch:
   ```bash
   git checkout -b optimization-phase-design --track origin/optimization-phase-design
   ```
   Or, if the remote branch does not yet exist, create locally and push with tracking:
   ```bash
   git checkout -b optimization-phase-design
   git push -u origin optimization-phase-design
   ```
3. Stage the documents:
   ```bash
   git add docs/roadmap.md docs/tech/benchmarking-and-optimization.md
   ```
4. Commit:
   ```bash
   git commit -m "Create benchmarking and optimization phase design"
   ```
5. Use pr-creation skill to create the PR:
   - Title: "Benchmarking and Optimization Phase Design".
   - Body includes references to the three-leaderboard model, comparison targets, and strategic success criteria.
   - Include lody session link via `https://lody.ai/leynos/sessions/${LODY_SESSION_ID}`.
6. Run Makefile gates if available:
   ```bash
   make lint
   make format-check
   ```
7. Ensure PR passes CI checks.

## Validation and acceptance

### Roadmap document acceptance

- [ ] Document is present at `docs/roadmap.md`.
- [ ] Roadmap covers at least four phases: foundation, expansion, regression gates, optimization loop.
- [ ] Each phase includes named milestones and success criteria.
- [ ] Dependencies between phases are explicit.
- [ ] The three-leaderboard philosophy is mentioned and briefly explained.
- [ ] Success metrics for the benchmarking phase are quantifiable and observable.
- [ ] Document passes British English style check.

### Technical design document acceptance

- [ ] Document is present at `docs/tech/benchmarking-and-optimization.md`.
- [ ] Benchmark philosophy section explains why three leaderboards prevent misinterpretation.
- [ ] Comparison targets table lists frameworks, modes, and rationales.
- [ ] Benchmark dimensions cover framework, handler, workload, concurrency, and settings.
- [ ] Metrics schema includes at least: caller_ns_per_call, records_enqueued/drained_per_second, end-to-end latencies, flush time, memory, drops.
- [ ] Repository structure for `benchmarks/` is specified with paths for adapters, cases, sinks, runner, schema, compare, report.
- [ ] Fairness rules are explicit (architectural parity, idiomatic vs. parity modes, disabled-log handling).
- [ ] Measurement protocols include warm-up, pyperf invocation, correctness verification, and JSON/markdown export.
- [ ] At least five concrete benchmark groups are named (disabled hot path, enabled null, I/O, batching, socket, saturation, configuration).
- [ ] Optimization playbook provides bottleneck classification and targeted fixes.
- [ ] Regression policy specifies thresholds by benchmark class.
- [ ] V0 implementation plan lists a narrow but meaningful starting suite.
- [ ] Document includes code examples for adapter interface and metrics schema.
- [ ] Document passes British English style check.

### PR acceptance

- [ ] PR is created and visible on GitHub.
- [ ] PR title is "Benchmarking and Optimization Phase Design" or similar.
- [ ] PR body includes a summary of the roadmap and design, key decisions, and strategic success criteria.
- [ ] PR body includes a `## References` section with lody session link.
- [ ] All commits have passed linting and formatting checks.
- [ ] All documents are properly staged and committed.

## Idempotence and recovery

- All steps are idempotent: re-running them does not introduce drift or conflicts.
- If branch creation fails, the local branch can be deleted and recreated: `git branch -D optimization-phase-design; git checkout -b optimization-phase-design --track origin/optimization-phase-design`.
- If document writing is interrupted, the skill can be re-invoked to resume from the same section.
- If the PR creation fails, the branch and commits remain valid and the PR can be created manually using the GitHub web interface.

## Artifacts and notes

**Key references from user specification:**

- Three leaderboards: caller-visible cost, end-to-end completion, diagnostic internals.
- Comparison targets: stdlib sync, stdlib QueueHandler/QueueListener, picologging (sync and queue), loguru (enqueue=False and enqueue=True), femtologging default and tuned variants.
- Benchmark philosophy: never compare asynchronous caller latency with synchronous end-to-end work without saying so loudly.
- V0 suite: disabled_literal, disabled_args, enabled_literal_null, enabled_args_null, file 1/8/32-thread sync vs. queue, burst_then_flush, slow_consumer_saturation, config benchmarks.

**Firecrawl research findings:**

- pyperf: automatic calibration, multiple worker processes, instability detection, JSON format, metadata collection, comparison tooling. Recommended for Python benchmarks.
- picologging: 4–17× faster than stdlib logging; drop-in API-compatible; Microsoft-maintained. Queue mode availability to be confirmed.
- loguru: `enqueue=True` makes logging non-blocking; docs note it uses a multiprocessing-safe queue. `logger.complete()` to wait for queued messages (to be verified).
- Python stdlib: QueueHandler and QueueListener available since Python 3.2; QueueListener spawns an internal thread listening to the queue.

## Interfaces and dependencies

**Core dependencies for benchmarking code:**

- pyperf: for Python benchmarks (calibration, JSON output, comparison).
- Criterion: for Rust internals (already in use; config.rs demonstrates setup).
- crossbeam-channel: for MPSC queue simulation in benchmarks (already a project dependency).
- MessagePack: for socket serialization benchmarks (if not already a dependency, review for inclusion).

**Module interfaces to define:**

In `benchmarks/femtobench/adapters/base.py`:

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class LoggingAdapter(ABC):
    """Base class for logging framework adapters."""
    
    @abstractmethod
    def configure(self, level: int, handler_type: str, **kwargs) -> None:
        """Configure the framework with a handler."""
        pass
    
    @abstractmethod
    def log_message(self, level: int, message: str, *args, **kwargs) -> None:
        """Log a message. Subclass decides whether to block or queue."""
        pass
    
    @abstractmethod
    def flush_and_close(self) -> None:
        """Flush and close all handlers."""
        pass
    
    @abstractmethod
    def get_metrics(self) -> Dict[str, Any]:
        """Return a dict of metrics collected during the run."""
        pass
```bash

In `benchmarks/femtobench/schema.py`:

```python
from dataclasses import dataclass
from typing import Dict, Any, List

@dataclass
class BenchmarkMetrics:
    """Standardised metrics schema for all benchmarks."""
    caller_ns_per_call: float
    records_enqueued_per_second: float
    records_drained_per_second: float
    end_to_end_ns_p50: float
    end_to_end_ns_p90: float
    end_to_end_ns_p99: float
    end_to_end_ns_p999: float
    flush_ns: float
    shutdown_drain_ns: float
    bytes_written_per_second: float
    drops: int
    max_queue_depth: int
    rss_peak_bytes: int
    allocations_per_record: int
    cpu_cycles_per_record: int
    context_switches: int
    syscalls_per_record: int
    framework: str
    handler_type: str
    workload: str
    concurrency: int
    queue_capacity: int
    batch_capacity: int
```bash

---

## Revision note

Initial draft completed 2026-06-17. Plan covers research, document creation,
style correction, branch management, and PR creation. Status is APPROVED and
ready for execution.
