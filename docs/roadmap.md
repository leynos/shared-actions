# Benchmarking and Optimization Phase Roadmap

Status: DRAFT  
Audience: Maintainers and contributors  
Last Updated: 2026-06-17

## Overview

Femtologging's queue-based architecture fundamentally differs from traditional synchronous logging. To credibly demonstrate its performance characteristics and guide optimizations, femtologging requires an evidence-based benchmarking programme organised around three separate leaderboards: caller-visible latency, end-to-end completion, and diagnostic internals.

This roadmap establishes benchmarking and optimisation as a core development phase, with four major stages: foundational infrastructure, expanded comparison matrix, internal performance laboratory, and evidence-driven optimisation cycles.

## Goals

- Establish a credible, architecture-aware comparison suite demonstrating femtologging's caller-latency advantage over synchronous handlers and fair comparison with async-capable frameworks (stdlib QueueHandler, loguru, picologging).
- Build an internal performance laboratory using Criterion (Rust) and pyperf (Python) to isolate and guide high-impact optimisations.
- Define regression gates to prevent performance regressions across future changes.
- Provide clear public reporting that explains why three leaderboards prevent misleading comparisons.

## Non-goals

- Formal performance SLAs or guarantees.
- Third-party benchmarking platform integrations (v1 future work).
- Optimisations to specific bottlenecks (those are PRs guided by the lab; this phase builds the lab).

---

## Phase 1: Benchmarking Foundation

**Idea:** Establish adapter layer, measurement protocols, and a proof-of-concept v0 suite to validate that pyperf + Criterion can reliably measure the three leaderboards and that the methodology is sound.

### 1.1 Adapter layer and metrics schema

Define the canonical interface for logging framework adapters and standardise metrics collection.

**Workstream question:** Can we abstract logging framework differences behind a clean interface?

**Tasks:**

- Define adapter base class (`LoggingAdapter` ABC) with methods: `configure()`, `log_message()`, `flush_and_close()`, `get_metrics()`.
- Implement adapters for stdlib logging (sync and QueueHandler/QueueListener), picologging (sync), loguru (enqueue=False and enqueue=True), femtologging.
- Define canonical metrics schema (JSON) covering caller latency, throughput, memory, drops, and timing percentiles.
- Implement metrics encoder/decoder for pyperf JSON export.

**Success:**

- Adapters pass unit tests confirming configuration and message logging work as expected.
- Metrics schema can represent all required dimensions without loss of information.
- Adapters produce consistent output format (JSON with defined schema) across all frameworks.

### 1.2 Measurement protocols and correctness verification

Establish warm-up, pyperf invocation, and post-run verification procedures.

**Workstream question:** Can we collect reliable measurements and verify correctness (record counts, no unexpected drops, frame integrity)?

**Tasks:**

- Implement warm-up harness to stabilise CPU cache and scheduler before benchmarks.
- Integrate pyperf.Runner for calibration, multiple worker processes, and instability detection.
- Define correctness checks: record count verification, expected byte count, no malformed frames, no unexpected drops.
- Implement JSON export with metadata (Python version, CPU, kernel, filesystem, commit SHA).
- Implement markdown table generation for summary reports.

**Success:**

- Smoke benchmarks run reproducibly with <5% coefficient of variation (instability check).
- Correctness verification catches seeded errors (e.g., dropped records, malformed frames).
- Output JSON validates against schema and includes all metadata.

### 1.3 V0 smoke suite

Implement the narrowest meaningful set of benchmarks to prove methodology.

**Workstream question:** Do the three leaderboards reveal the expected patterns (caller latency vs. end-to-end, femtologging wins on caller latency)?

**Tasks:**

- Disabled logs (literal, cheap args): no framework work; level check overhead only.
- Enabled null handler: record creation and metadata without I/O.
- Single-threaded file I/O (tmpfs): show caller latency vs. end-to-end difference.
- Configuration speed: `basicConfig`, `dictConfig`, builder patterns.
- Produce three summary tables: caller latency, end-to-end completion, diagnostic internals.

**Success:**

- V0 suite executes without error on Python 3.12+.
- Femtologging caller latency is <10 µs for enabled workloads.
- Comparison tables show expected patterns: stdlib sync higher end-to-end, femtologging lower caller latency.
- Results exported as JSON and markdown.

### 1.4 Regression gate configuration

Configure CI to enforce regression thresholds.

**Workstream question:** Can we block regressions automatically?

**Tasks:**

- Define regression thresholds by category (disabled >3%, enabled null >5%, caller latency >5%, memory >10%).
- Integrate pyperf.compare_to() for statistical significance testing.
- Configure CI to run smoke suite on every commit and block merges if thresholds breached.
- Document threshold rationale.

**Success:**

- CI runs smoke suite successfully.
- Manual regression (introduced by a test change) is caught and blocks merge.
- Threshold rationale documented in decision log.

---

## Phase 2: Benchmarking Expansion

**Idea:** Extend the suite to cover multi-threaded workloads, I/O-heavy scenarios, and saturation conditions, demonstrating scaling and architectural differences across the full problem space.

### 2.1 Concurrency matrix

Sweep producer thread counts and message totals.

**Workstream question:** How do frameworks scale with increasing producer concurrency?

**Tasks:**

- Implement 1, 2, 4, 8, 16, 32, and 2×CPU producer-thread variants.
- Run fixed-total message counts (e.g., 100K across all producers) to measure overhead scaling.
- Run fixed-per-producer counts (e.g., 10K per producer) to measure saturation.
- Measure caller latency and end-to-end completion for each configuration.

**Success:**

- Concurrency matrix completes without error.
- Scaling shows expected patterns: femtologging maintains low caller latency across thread counts; synchronous handlers degrade under contention.

### 2.2 File handler variants

Test diverse file configurations and batching strategies.

**Workstream question:** What batching and queue capacity trade-offs optimise file I/O?

**Tasks:**

- Tmpfs and real SSD targets (avoid terminal I/O).
- Rotating file without and with actual rollover.
- Queue capacity sweep (1K, 8K, 65K, default).
- Batch capacity sweep (1, 4, 16, 64, 256).
- Measure syscalls/record, bytes/syscall, flush latency.

**Success:**

- File variants complete without error.
- Batching and queue settings show clear trade-offs between latency and throughput.

### 2.3 Socket handler

Implement loopback TCP and Unix socket benchmarks.

**Workstream question:** How do socket I/O latencies compare across frameworks? Are frames correctly serialised?

**Tasks:**

- Local TCP and Unix socket loopback servers.
- Validate frame format (four-byte big-endian length prefix, MessagePack payload).
- Measure serialisation and transmission latencies.
- Payload size sweep (32 B, 128 B, 1 KiB).

**Success:**

- Socket benchmarks complete and validate frame correctness.
- End-to-end latencies account for socket serialisation overhead.

### 2.4 Saturation and backpressure

Test queue overflow, consumer stalling, and recovery.

**Workstream question:** How predictable and safe is femtologging under saturation?

**Tasks:**

- Bounded queue with fast consumer (baseline throughput).
- Bounded queue with slow consumer (artificial delay, measure blocking, drops, recovery).
- Bounded queue with stalled consumer (measure caller blocking and drop count).
- Burst then flush (short spike followed by idle, measure tail latency).
- Producer spike during file rotation (measure impact on other workloads).
- Socket disconnect-reconnect (measure reconnection latency and drops).
- Record drops, blocked time, drain time, memory growth.

**Success:**

- Saturation tests show bounded and predictable behaviour.
- Drops occur only when specified by overflow policy; no silent data loss.
- Recovery after stall is clean and complete.

### 2.5 Structured fields and context

Benchmark metadata overhead.

**Workstream question:** How much overhead do key-value metadata and scoped context add?

**Tasks:**

- Enabled benchmarks with 2, 4, 8 key-value pairs.
- Scoped context (threadlocal + inline override).
- Filter evaluation (accept and reject paths).
- Exception logging with chained exceptions and exception groups.
- Stack info capture and formatting.

**Success:**

- Structured workloads show expected overhead (metadata allocation, context merge).
- Filter rejection path is cheap (fast escape).

---

## Phase 3: Rust Internals Laboratory

**Idea:** Build Criterion-based microbenchmarks isolating hot paths and worker costs, providing data to guide optimisations without re-running the full Python suite for every hypothesis.

### 3.1 Hot-path microbenchmarks

Criterion benches for producer-side critical operations.

**Workstream question:** Which producer-side operations dominate caller latency?

**Tasks:**

- Level check (effective level lookup, disabled short-circuit).
- Record creation (metadata allocation, timestamp capture).
- Channel send (single producer, multiple producers, queue fullness impact).
- PyO3 conversion costs (argument extraction, type conversion).

**Success:**

- Microbenchmarks identify top 3 bottlenecks (e.g., allocations, timestamp, channel contention).
- Results guide v0 optimisation priorities.

### 3.2 Formatter benchmarks

Isolate formatting cost from I/O.

**Workstream question:** Which format patterns are expensive?

**Tasks:**

- Literal message (minimal).
- Arguments (2, 4, 8).
- Structured fields (JSON-ish format).
- Exceptions (short, chained, grouped).
- Stack info.

**Success:**

- Formatter profiles show cost per operation.
- Buffer reuse strategy is measurable.

### 3.3 Worker benchmarks

Measure consumer-side throughput and latency.

**Workstream question:** What is the throughput ceiling for each worker type?

**Tasks:**

- File worker drain-loop with batch capacity sweep (1, 4, 16, 64, 256).
- Syscall counting (write, flush, fsync).
- Socket serialisation into reusable buffers.
- Context switches and thread scheduling overhead.

**Success:**

- Worker profiles show throughput ceiling and latency per record.
- Batching strategy trade-offs are quantified.

### 3.4 Flamegraph and perf-stat runs

Select representative cases for in-depth profiling.

**Workstream question:** Where is the CPU actually spent?

**Tasks:**

- Flamegraph on hot-path cases (e.g., 1M 32-byte log calls, 8 threads).
- perf stat on file and socket workers (syscalls, context switches, CPU cycles).

**Success:**

- Flamegraphs pinpoint code lines responsible for hotspots.
- perf stat data informs allocation and syscall optimisation targets.

---

## Phase 4: Optimization Loop and Regression Gates

**Idea:** Establish evidence-driven optimization flow: measure → classify bottleneck → hypothesise fix → implement → re-measure → gate. Each optimization is backed by benchmark data and monitored against regression thresholds.

### 4.1 Regression threshold configuration

Define per-category thresholds and baseline.

**Workstream question:** Which regressions matter, and which are acceptable trade-offs?

**Tasks:**

- Quantify thresholds: disabled >3%, enabled null >5%, caller latency >5%, throughput >5%, memory >10%, drops always fail, configuration >10%.
- Run full benchmark suite on pinned hardware (minimal other load) and commit baseline JSON.
- Document threshold rationale in ADR.

**Success:**

- Baseline benchmarks committed to repository.
- Thresholds codified and documented.

### 4.2 Optimization PR template

Define expected format for optimization PRs.

**Workstream question:** How do we ensure optimization PRs include sufficient evidence?

**Tasks:**

- Template: benchmark case, before/after numbers, relative change %, p-value / pyperf significance result, correctness checks, memory impact, complexity cost, workload rationale.
- Integrate into PR checklist and documentation.

**Success:**

- Optimization PRs follow template and cite benchmark evidence.

### 4.3 First optimization cycle

Target one high-impact area and demonstrate the loop.

**Workstream question:** Can we measurably improve a bottleneck area and defend against regression?

**Tasks:**

- Select one area (e.g., allocations per record or formatter buffer reuse) based on Phase 3 lab data.
- Implement smallest change addressing the bottleneck.
- Re-run affected benchmarks.
- Verify improvement meets significance threshold.
- Gate regression test.
- Document decision log and trade-offs.

**Success:**

- First optimization merged with measurable improvement.
- Regression gate catches introduced slowdown and blocks merge.

### 4.4 Public reporting automation

Generate summary tables and trend lines.

**Workstream question:** How do we communicate benchmark results publicly?

**Tasks:**

- Markdown table generation from JSON results.
- Trend lines by commit (showing slow creep detection).
- "Benchmark meaning" primer (why three leaderboards, when to trust each number).

**Success:**

- Public comparison tables are generated and integrated into release notes or documentation.
- Users understand the methodology and can interpret results correctly.

---

## Phase 5: Deferred Work

- Dashboard and time-series tracking (v1).
- Per-machine class baseline management (CI runner vs. bare-metal).
- Integration with GitHub/CI for automated comparison comments on PRs.
- Advanced diagnostics: allocation profiling, GC overhead, CPU cycle attribution.
- Optimizations to specific bottlenecks (guided by lab, implemented as separate PRs).

---

## Dependencies and sequencing

- Phase 1 unblocks Phase 2 (adapter and runner are shared infrastructure).
- Phase 2 informs Phase 3 (Python results guide which Rust internals to profile).
- Phase 3 informs Phase 4 (lab data guides optimization priorities).
- Phases 2, 3, and 4 can run in parallel after Phase 1 is complete.

## Success metrics

- V0 smoke suite executes without error on Python 3.12+.
- Femtologging caller latency <10 µs for queue-based handlers.
- Full concurrency matrix shows consistent scaling.
- Regression gate successfully blocks a >threshold regression.
- At least one optimization guided by lab results is merged.
- Public comparison tables are generated and documented.
- External users can interpret benchmark results without confusion about architecture.
