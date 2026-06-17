# Benchmarking and Optimization Strategy

Status: DRAFT  
Audience: Developers, reviewers, users interpreting benchmarks  
Last Updated: 2026-06-17

## Executive summary

Femtologging's queue-based architecture fundamentally differs from synchronous
logging libraries. This design document specifies a three-leaderboard
benchmarking methodology, fair comparison targets, and an optimization playbook
to guide performance improvements.

The three leaderboards are:

1. **Caller-visible cost:** Time spent inside the logging call on producer
threads (where femtologging should excel).
2. **End-to-end completion:** Wall-clock time from first log call to final
flush and drain (prevents asynchronous libraries from hiding work).
3. **Diagnostic internals:** Granular costs (record creation, channel send,
formatter, worker drain) guiding optimizations.

Using pyperf for Python and Criterion for Rust, this strategy measures
architecture-aware fairness, establishes regression gates, and provides the
evidence base for optimization PRs.

---

## 1. Benchmark Philosophy

### 1.1 The three-leaderboard model

**Problem:** A single benchmark conflates asynchronous caller latency with
end-to-end work, producing misleading rankings. An asynchronous library might
report fast caller latency while hiding a mountain of work in background
threads.

**Solution:** Three separate leaderboards prevent this conflation.

#### Leaderboard 1: Caller-visible cost

**Measures:** How long the application thread spends inside the logging call.

**Why it matters:** Femtologging's design pushes formatting and I/O to consumer
threads through MPSC queues, minimising producer thread time. For frameworks
using queue modes (stdlib QueueHandler/QueueListener, loguru enqueue=True), this
is the fair comparison.

**Interpretation:**
- Femtologging should dominate here when compared fairly against queue-capable frameworks.
- Synchronous frameworks (stdlib direct, picologging sync) will show higher values because they do formatting and I/O on the caller thread.

**Not included:**
- Consumer thread work (formatting, I/O).
- Background drain or flush operations.

#### Leaderboard 2: End-to-end completion

**Measures:** Wall-clock time from the first log call until all records have
reached the sink and the logger has flushed or drained.

**Why it matters:** Prevents asynchronous libraries from winning by hiding
cost. For femtologging, this means "enqueue all records, flush, drain, verify
all records reached output."

**Interpretation:**
- Synchronous frameworks with efficient formatting may win here (no queue overhead, single pass).
- Asynchronous frameworks must account for queue depth, batch processing, and flush latency.
- End-to-end reveals the true total cost, including consumer overhead.

**Semantics:**
- For stdlib QueueHandler: stop or close the listener after the queue drains.
- For loguru enqueue=True: call `logger.complete()` to wait for queued messages.
- For femtologging: call flush() and drain(), verifying all records reached output.

#### Leaderboard 3: Diagnostic internals

**Measures:** Granular costs of individual operations (record creation, channel
send, formatter, worker drain) and resource metrics (memory, allocations,
context switches, syscalls).

**Why it matters:** Guides optimization work without re-running the full suite
for every hypothesis. Points to the hot metal.

**Interpretation:**
- Not directly comparable across frameworks (each may measure different things).
- Used internally to decide "should we optimize formatter or channel send next?"
- Public reporting should be minimal; emphasis is on femtologging's internal labs.

### 1.2 Architectural awareness

Frameworks supporting async-via-queue have fundamentally different latency
trade-offs. Comparing a synchronous direct handler with an async queue handler
produces meaningless results.

**Architectural classification:**

| Framework       | Mode                 | Producer thread | Consumer thread | Fair comparison                                |
|:---|:---|:---|:---|:---|
| stdlib          | Direct handler       | Does formatting, I/O | N/A             | Only with other direct handlers                |
| stdlib          | QueueHandler         | Enqueues only   | QueueListener   | With loguru enqueue=True and femtologging      |
| picologging     | Direct handler       | Does formatting, I/O | N/A             | Only with other direct handlers                |
| loguru          | enqueue=False        | Does formatting, I/O | N/A             | Only with other direct handlers                |
| loguru          | enqueue=True         | Enqueues only   | Background      | With stdlib QueueHandler and femtologging      |
| femtologging    | Default              | Enqueues only   | Rust workers    | With stdlib QueueHandler and loguru enqueue=True |

**Fairness rule:** Never put a queue-based framework's caller latency on the
same chart as a synchronous framework's end-to-end time. Label the leaderboard
clearly so a reader cannot make that mistake.

---

## 2. Comparison Targets

### 2.1 Framework selection and rationale

| Framework       | Mode                            | Why included                                                                                              |
|:---|:---|:---|
| stdlib logging  | Direct handler                  | User baseline; shows traditional caller-blocking cost                                                   |
| stdlib logging  | QueueHandler + QueueListener    | Fairer async architecture match; stdlib-native async pattern                                            |
| picologging     | Direct handler                  | Drop-in high-performance replacement (4–10× faster than stdlib)                                         |
| picologging     | Queue mode (if available)       | Fair queue-based comparison if API-compatible                                                           |
| loguru          | enqueue=False (idiomatic)       | Default loguru usage; shows what users experience out of the box                                        |
| loguru          | enqueue=True (async-capable)    | Fair queue-based comparison; loguru docs promise non-blocking with enqueue=True                         |
| femtologging    | Default handler model           | The product; what users adopt                                                                           |
| femtologging    | Tuned variants                  | Batch size, queue capacity, formatter choices, socket/file settings; guides optimization               |

### 2.2 Implementation guidance for adapters

Each adapter must enforce semantic equivalence whilst allowing architectural
honesty.

**Disabled logs (level too low to generate a record):**
- Level check must happen first; no record creation, no timestamp, no metadata.
- Measure caller time only.
- No argument evaluation after level check.

**Enabled logs with NullHandler:**
- Record created, no formatting, no I/O.
- Measure record creation cost and metadata overhead.

**Enabled logs with file/stream:**
- Full pipeline: record, format, I/O.
- For sync frameworks: measured on caller thread.
- For queue frameworks: caller measure excludes consumer work.

**Configuration:**
- All frameworks use the same log level (e.g., INFO).
- All use the same message format string (e.g., "message=%s value=%d").
- No framework-specific prettification, colours, or caller introspection unless explicitly benchmarking those features.

**Preconfigured defaults removal:**
- loguru comes with a preconfigured stderr handler; remove it before adding benchmark handlers.
- stdlib loggers may propagate to root; disable propagation unless testing it explicitly.

---

## 3. Benchmark Dimensions

The core matrix varies framework, handler, workload, concurrency, and
queue/batch settings.

### 3.1 Framework

- stdlib.logging (sync and queue)
- picologging (sync; queue if available)
- loguru (enqueue=False and enqueue=True)
- femtologging (default; variants)

### 3.2 Handler type

- **NullHandler:** No formatting or output; isolates record creation overhead.
- **Stream to /dev/null:** Minimises disk I/O variance; still incurs format + write.
- **File on tmpfs:** Avoids SSD randomness; faster than disk.
- **File on real SSD or HDD:** Shows production-like I/O cost.
- **Rotating file without rollover:** File handler with size limit set high; no actual rotation.
- **Rotating file with rollover:** Actual rotation triggered; measures rotation cost.
- **Socket to loopback:** Local TCP or Unix socket; isolates serialization and network stack.
- **Slow sink simulation:** Artificial delay; measures backpressure and caller blocking.

### 3.3 Workloads

| Workload                  | Example                                 | Purpose                                               |
|:---|:---|:---|
| Disabled literal          | debug("constant") with level INFO       | Fast level check, no record creation                 |
| Disabled cheap args       | debug("x=%s", x) with level INFO        | Lazy formatting, argument handling (not evaluated)   |
| Disabled eager f-string   | debug(f"x={expensive()}") with INFO     | User-side eagerness, not framework overhead          |
| Enabled literal           | info("constant")                        | Record creation baseline                             |
| Enabled args              | info("x=%s y=%s", x, y)                 | Formatter and argument handling                      |
| Structured fields         | Context: request_id, user_id, path      | Key-value metadata overhead (femtologging feature)   |
| Scoped context            | threadlocal + inline override           | Context merge and propagation overhead               |
| Filtered (accepted)       | Logger/root filter that returns True    | Filter hot-path cost                                 |
| Filtered (rejected)       | Logger/root filter that returns False   | Early escape cost                                    |
| Exception logging         | exc_info=True, chained, exception group | Expensive but operationally vital                    |
| Stack info                | stack_info=True equivalent              | Frame capture and serialization cost                 |
| Socket serialisation      | Local TCP/Unix socket sink              | MessagePack/framing vs. stdlib pickle                |
| Burst logging             | Short burst, then idle                  | Queue wake-up, batch shrinkage, tail latency         |
| Sustained logging         | Millions of records                     | Throughput, batching, memory, drops                  |
| Slow consumer             | Artificial sink delay                   | Backpressure, drops, caller blocking, shutdown      |

### 3.4 Concurrency

- Producer threads: 1, 2, 4, 8, 16, 32, 2×CPU count.
- Two message distribution strategies:
  - Fixed total (e.g., 100K records across all threads): shows overhead scaling.
  - Fixed per-producer (e.g., 10K per thread): shows saturation behavior.

### 3.5 Femtologging-specific tuning parameters

| Parameter       | Values to sweep                                 | Rationale                                          |
|:---|:---|:---|
| Queue capacity  | 1K, 8K, 65K, default                           | Queue depth affects latency and memory             |
| Batch capacity  | 1, 4, 16, 64, 256                              | Batch size affects throughput and tail latency    |
| Flush policy    | Per-record, periodic, explicit only             | Tradeoff between latency and throughput           |
| Formatter       | Minimal, default, JSON-ish, exception-heavy    | Format complexity affects worker throughput        |
| Timestamp mode  | Disabled, wall-clock, high-resolution          | Timestamp cost is measurable                       |
| Overflow policy | Block, drop                                     | Backpressure vs. data loss trade-off              |

---

## 4. Metrics Schema

### 4.1 Canonical metrics

Every benchmark must produce structured JSON with these fields:

| Field                      | Unit   | Meaning                                                     |
|:---|:---|:---|
| caller_ns_per_call         | ns     | Time spent in logging call on producer thread               |
| records_enqueued_per_second| rec/s  | Producer-side throughput (records/second enqueued)         |
| records_drained_per_second | rec/s  | Consumer-side throughput (records/second processed)        |
| end_to_end_ns_p50          | ns     | Percentile 50 end-to-end latency (first call to flush)     |
| end_to_end_ns_p90          | ns     | Percentile 90 end-to-end latency                           |
| end_to_end_ns_p99          | ns     | Percentile 99 end-to-end latency                           |
| end_to_end_ns_p999         | ns     | Percentile 99.9 end-to-end latency                         |
| flush_ns                   | ns     | Time to flush or complete after producers finish           |
| shutdown_drain_ns          | ns     | Time to stop listeners/workers cleanly                     |
| bytes_written_per_second   | B/s    | Throughput for file/socket sinks                           |
| drops                      | count  | Records lost due to overflow (must be zero unless tested) |
| max_queue_depth            | count  | Peak queue size observed (if exposed by framework)         |
| rss_peak_bytes             | bytes  | Peak process memory                                        |
| allocations_per_record     | count  | Allocations attributed to record creation (if profiled)    |
| cpu_cycles_per_record      | cycles | CPU cycles per record (perf stat)                          |
| context_switches           | count  | Thread context switches during run                         |
| syscalls_per_record        | count  | System calls per record (file/socket I/O)                 |

### 4.2 Metadata

Every benchmark result must include:

```json
{
  "metadata": {
    "commit_sha": "...",
    "python_version": "3.12.1",
    "framework": "femtologging",
    "framework_version": "...",
    "handler_type": "file",
    "workload": "enabled_args",
    "concurrency": 8,
    "queue_capacity": 8192,
    "batch_capacity": 16,
    "os": "Linux",
    "kernel": "6.12.0-124.27.1.el10_1.x86_64",
    "cpu_model": "AMD Ryzen...",
    "cpu_count": 6,
    "memory_gb": 64,
    "filesystem": "tmpfs",
    "timestamp": "2026-06-17T14:30:00Z"
  }
}
```python

---

## 5. Repository Structure

```python
benchmarks/
├── README.md                           # Benchmarking guide
├── pyproject.toml                      # Python project config
├── femtobench/
│   ├── __init__.py
│   ├── adapters/                       # Framework adapters
│   │   ├── base.py                     # LoggingAdapter ABC
│   │   ├── stdlib_logging.py           # stdlib.logging (sync)
│   │   ├── stdlib_queue.py             # stdlib QueueHandler/QueueListener
│   │   ├── picologging_sync.py         # picologging (sync)
│   │   ├── picologging_queue.py        # picologging (queue, if available)
│   │   ├── loguru_sync.py              # loguru (enqueue=False)
│   │   ├── loguru_enqueue.py           # loguru (enqueue=True)
│   │   └── femtologging.py             # femtologging
│   ├── cases/                          # Workload definitions
│   │   ├── disabled.py                 # Disabled logs
│   │   ├── enabled_null.py             # Enabled with NullHandler
│   │   ├── file.py                     # File I/O benchmarks
│   │   ├── rotating_file.py            # Rotating file handler
│   │   ├── stream.py                   # Stream (stdout, stderr, /dev/null)
│   │   ├── socket.py                   # Socket handler
│   │   ├── filters.py                  # Filter hot-path tests
│   │   ├── exceptions.py               # Exception logging
│   │   ├── stack_info.py               # Stack trace capture
│   │   ├── structured.py               # Key-value metadata
│   │   ├── context.py                  # Scoped context
│   │   ├── saturation.py               # Backpressure and overflow
│   │   └── config.py                   # Configuration benchmarks
│   ├── sinks/
│   │   ├── null.py                     # NullHandler
│   │   ├── counting.py                 # Count records without I/O
│   │   ├── slow.py                     # Artificial delay sink
│   │   └── socket_server.py            # Loopback socket server
│   ├── runner.py                       # pyperf.Runner integration
│   ├── schema.py                       # Metrics dataclass and JSON schema
│   ├── compare.py                      # Statistical comparison and tables
│   └── report.py                       # Markdown report generation
├── results/
│   └── .gitkeep
└── Makefile                            # Targets: bench-smoke, bench-python, bench-rust, bench-compare
```python

---

## 6. Adapter Layer Design

### 6.1 LoggingAdapter ABC

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class AdapterConfig:
    """Configuration for a logging adapter."""
    level: int                          # logging.INFO, etc.
    handler_type: str                   # 'null', 'file', 'socket', etc.
    handler_kwargs: Dict[str, Any]      # Handler-specific options
    formatter_style: str                # '%()', '{}', or similar
    queue_capacity: Optional[int]       # For async frameworks
    batch_capacity: Optional[int]       # For femtologging
    overflow_policy: str                # 'block' or 'drop'

class LoggingAdapter(ABC):
    """Base class for logging framework adapters."""
    
    @abstractmethod
    def configure(self, config: AdapterConfig) -> None:
        """Configure the framework with the given configuration."""
        pass
    
    @abstractmethod
    def log_message(self, level: int, message: str, *args, **kwargs) -> None:
        """Log a message at the specified level.
        
        For benchmarks, this must be a true logging call, not a bypassed path.
        Arguments must be evaluated (as the framework would normally do).
        """
        pass
    
    @abstractmethod
    def flush_and_close(self) -> None:
        """Flush all handlers and close cleanly.
        
        For queue-based frameworks, this must wait for queued messages
        to reach the sink before returning.
        """
        pass
    
    @abstractmethod
    def get_metrics(self) -> Dict[str, Any]:
        """Return metrics collected during the run."""
        pass
```python

### 6.2 Implementation expectations

Each adapter must:

1. **Enforce semantic equivalence:** Same level, format string, and output
destination as other frameworks.
2. **Measure honestly:** Don't bypass the framework's normal path to save
latency.
3. **Handle queue draining:** Queue-based frameworks must fully drain in
flush_and_close().
4. **Verify correctness:** Record count, no unexpected drops, no malformed
output.
5. **Collect metrics:** Record caller latency, throughput, memory, and
framework-specific diagnostics.

### 6.3 Fairness checks

Before every benchmark run:

- Confirm all frameworks log to the same destination (e.g., same tmpfs file or /dev/null).
- Confirm all use the same message format and level.
- Disable any framework-specific prettification, colours, or caller introspection.
- For loguru, remove the preconfigured stderr handler.
- For stdlib, disable propagation to root logger unless testing it.

---

## 7. Fairness Rules

### 7.1 Architectural parity

Queue-based and synchronous handlers are fundamentally different. Fairness
requires honest representation of both.

**For synchronous (direct) handlers:**
- Formatter and I/O happen on the caller thread.
- Caller latency includes all work.
- End-to-end is the same as caller latency (no background drain).

**For queue-based (async) handlers:**
- Only enqueue happens on caller thread.
- Caller latency excludes formatting and I/O.
- End-to-end includes queue drain and all consumer work.

Never mix these on the same chart without clear labels.

### 7.2 Idiomatic vs. parity mode

Run benchmarks in two configurations:

**Idiomatic mode:**
- Each library uses its recommended style (loguru uses `{}`, stdlib/picologging use `%`).
- Default settings (e.g., loguru includes a stderr handler by default).
- Shows what users actually experience.

**Parity mode:**
- All libraries use the same message format and output destination.
- No colours, no exception prettification, no caller introspection.
- Isolates engine differences from stylistic choice.

Report both; parity mode data informs performance comparison.

### 7.3 Disabled-log argument handling

Python evaluates function arguments before the function is called.
`logger.debug("x=%s", expensive())` calls `expensive()` even if the log level
disables the record.

Benchmarks must account for this:

1. **Disabled literal:** `debug("constant")` — no arguments to evaluate.
2. **Disabled cheap args:** `debug("x=%s", x)` — argument exists but is cheap
(variable reference).
3. **Disabled eager f-string:** `debug(f"x={expensive()}")` — demonstrates
user-side eagerness, not framework overhead. Label this as "user expression
cost", not framework cost.

Framework comparison should focus on disabled literal and disabled cheap args;
eager f-strings are a user choice.

---

## 8. Measurement Protocols

### 8.1 Warm-up and calibration

Before each benchmark:

1. Run the workload once without timing (warm-up cache, scheduler).
2. Let pyperf calibrate the number of iterations to reach a target runtime
(default: 1 second per run).
3. Repeat the benchmark multiple times with pyperf managing iteration count and
worker processes.

### 8.2 pyperf invocation

```python
import pyperf

runner = pyperf.Runner()
runner.bench_func('benchmark_name', bench_func, *args)

# pyperf will:
# - Run multiple worker processes (default: 5)
# - Collect samples (default: 3 per worker)
# - Calibrate iteration count automatically
# - Detect instability and warn if CV > 10%
# - Export JSON with metadata
```python

### 8.3 Correctness verification

After each benchmark run:

```python
def verify_correctness(adapter, expected_records: int, expected_bytes: int) -> None:
    """Verify benchmark correctness."""
    metrics = adapter.get_metrics()
    
    assert metrics['record_count'] == expected_records, \
        f"Record count mismatch: {metrics['record_count']} != {expected_records}"
    assert metrics['drops'] == 0, \
        f"Unexpected drops: {metrics['drops']}"
    assert metrics['bytes_written'] >= expected_bytes * 0.9, \
        f"Output size too small: {metrics['bytes_written']} < {expected_bytes * 0.9}"
```python

### 8.4 JSON export and markdown generation

```python
import json
from datetime import datetime

result = {
    "framework": "femtologging",
    "workload": "enabled_args",
    "metrics": metrics,
    "metadata": {
        "commit_sha": os.popen("git rev-parse HEAD").read().strip(),
        "python_version": sys.version,
        "timestamp": datetime.utcnow().isoformat(),
        # ... additional metadata
    }
}

# Export JSON
with open('results/benchmark.json', 'w') as f:
    json.dump(result, f, indent=2)

# Generate markdown table
print("| Framework | Caller latency (µs) | E2E latency (µs) |")
print("|---|---|---|")
for framework, metrics in results.items():
    print(f"| {framework} | {metrics['caller_ns_per_call']/1000:.2f} | ...")
```python

---

## 9. Concrete Benchmark Groups

### 9.1 Group A: Disabled hot path

**Goal:** Prove disabled logging costs nearly nothing.

**Cases:**
- disabled_literal: `debug("constant")` with level INFO
- disabled_args: `debug("x=%s", x)` with level INFO
- disabled_guarded_expensive_args: as above, but expensive() is guarded by explicit `if logger.isEnabledFor(DEBUG):`
- disabled_eager_fstring: `debug(f"x={expensive()}")` (demonstrates user eagerness)

**Metrics:** caller_ns_per_call, allocations, context switches.

**Expected:** <50 ns/call for frameworks with efficient level checks. Expensive
args case should be faster if guarded.

### 9.2 Group B: Enabled no-output path

**Goal:** Isolate record creation without I/O.

**Cases:**
- enabled_literal_null: `info("constant")` with NullHandler
- enabled_args_null: `info("x=%s y=%s", x, y)` with NullHandler
- enabled_structured_null: key-value metadata with NullHandler
- enabled_context_null: scoped context with NullHandler
- enabled_filter_accept_null: filter evaluates and returns True
- enabled_filter_reject_null: filter evaluates and returns False

**Metrics:** caller_ns_per_call, allocations_per_record,
records_enqueued_per_second.

**Expected:** Femtologging shows minimal caller latency. Picologging sync and
loguru enqueue=False show higher latency (formatting overhead). Stdlib shows
baseline.

### 9.3 Group C: Direct I/O vs. queued I/O

**Goal:** Compare architecture honestly.

**Cases:**
- stream_to_devnull_sync (stdlib, picologging)
- stream_to_devnull_queue (stdlib QueueHandler, loguru enqueue=True)
- file_tmpfs_sync
- file_tmpfs_queue
- file_real_disk_sync
- file_real_disk_queue

**Metrics:** caller_ns_per_call, end_to_end_ns_p50/p99,
records_drained_per_second, bytes_written_per_second.

**Expected:** Sync handlers show high caller latency; queue handlers show lower
caller latency and higher end-to-end. Femtologging should lead in caller
latency; end-to-end depends on worker efficiency.

### 9.4 Group D: File batching

**Goal:** Optimise femtologging's consumer workers.

**Cases:** Vary batch_capacity (1, 4, 16, 64, 256) and producer_threads (1, 4,
16, 64) with file sink on tmpfs.

**Metrics:** records_per_second, syscalls_per_record, flush_ns,
shutdown_drain_ns, p99 caller latency.

**Expected:** Larger batch capacity improves throughput but may increase tail
latency under saturation. Trade-off data informs batching strategy.

### 9.5 Group E: Socket handler

**Goal:** Test serialisation and reconnection without network randomness.

**Cases:** TCP loopback and Unix socket, payload sizes 32 B / 128 B / 1 KiB,
batch capacity sweep.

**Metrics:** caller_ns_per_call, serialisation latency, frame correctness
(validate four-byte length prefix and MessagePack payload).

**Expected:** Socket overhead is predictable. Frame format is correct.

### 9.6 Group F: Saturation and backpressure

**Goal:** Avoid pleasant averages that hide catastrophe.

**Cases:**
- bounded_queue_fast_consumer (baseline throughput)
- bounded_queue_slow_consumer (artificial delay; measure blocking)
- bounded_queue_stalled_consumer (queue full; measure drops and recovery)
- burst_then_flush (short spike, then idle)
- producer_spike_while_rotating (measure interference)
- socket_disconnected_then_reconnect

**Metrics:** caller_ns_p50/p99/p999, drops, blocked_time_ns, drain_ns,
memory_growth_bytes.

**Expected:** Bounded and predictable behaviour. Drops only when policy allows.
Recovery is clean.

### 9.7 Group G: Configuration benchmarks

**Goal:** Measure startup cost.

**Cases:** Extend existing Criterion benches in
rust_extension/benches/config.rs. Add Python pyperf cases:
- femtologging_basicConfig
- femtologging_dictConfig
- femtologging_builder
- stdlib_basicConfig
- stdlib_dictConfig
- picologging_basicConfig
- loguru_configure

**Metrics:** Configuration time (ms).

**Expected:** Configuration is not the hot path, but fast startup aids
adoption. Femtologging builder should be competitive.

---

## 10. Optimization Playbook

When benchmarks reveal a bottleneck, use this playbook to guide investigation
and implementation.

### 10.1 Disabled logs are too slow (>50 ns/call expected baseline)

1. **Measure level lookup.** Is the effective level check fast? Cache misses?
Python interpreter overhead?
2. **Optimise level check first.** Use integer comparison, avoid frame
inspection or context lookups until after level check.
3. **Disable timestamp and metadata allocation** until after level check
(already required by design).

### 10.2 Enabled NullHandler is too slow

1. **Profile allocations.** PyO3 conversions? RecordMetadata allocation?
BTreeMap creation for empty key-value?
2. **Use lazy metadata.** Store None/empty singleton for fields until a
formatter or handler requires them.
3. **Avoid BTreeMap for zero or single fields.** Use SmallVec or inline small
map.
4. **Reuse buffers.** Python string creation in tight loops is expensive.

### 10.3 Multi-threaded enqueue slows down

1. **Benchmark channel capacity and contention.** crossbeam-channel performance
degrades under high contention.
2. **Try in order:**
   - Increase queue capacity (reduce lock contention).
   - Tune batch drain capacity.
   - Try non-blocking send with explicit drop accounting.
   - Shard queue by thread ID (if contention is the issue).
   - Profile context switches (perf stat).
3. **Do not swap channel crates.** ADR 004 chose crossbeam-channel for good
reasons; a swap is a last resort.

### 10.4 Formatting dominates

1. **Preparse format strings.** Cache the parsed format template.
2. **Reuse buffers.** Allocate once per worker, write into the same buffer.
3. **Avoid `format!` in loops.** Use write! into an existing buffer.
4. **Defer timestamp formatting.** Capture timestamp as a number; format only
if the formatter requests it.
5. **Specialise integer/float formatting.** Standard library formatting is
general-purpose; numeric-only paths are faster.

### 10.5 File throughput lags

1. **Eliminate accidental per-record flushes.** File::flush() after every write
kills throughput.
2. **Batch writes.** Collect multiple formatted records into a single write
call (ADR 004 treat this as follow-on work).
3. **Measure syscalls/record** (perf stat). If high, batching is the target.
4. **After batching, revisit vectored I/O.** write_vectored() can further
reduce syscall count.

### 10.6 Socket throughput lags

1. **Separate serialisation from socket write.**
   - Bench MessagePack serialisation into a reusable buffer without I/O.
   - Bench loopback socket write with prebuilt frames.
   - Combine them to find the bottleneck.
2. **Reuse serialisation buffers.** Allocate once per worker; clear and reuse.
3. **Batch frames.** Collect multiple serialised records into one write call.
4. **Avoid per-record frame-header allocation.** Reuse a template for the
four-byte length prefix.

### 10.7 Filters are expensive

1. **Keep fast path brutally plain.** Level check → filters. No other branches.
2. **Store filter collections in Arc<[...]>.** Readers do not need a lock.
3. **Split Rust-native filters from Python callbacks.** Callbacks cross PyO3
boundary; benchmark them separately.

### 10.8 Memory grows under bursts

1. **Add a long-running soak test.** 10 minutes at fixed log rate, periodic
bursts. Measure RSS every second.
2. **Profile allocations.** Queue capacity retained? Buffers not reclaimed?
Exception payloads pinned?
3. **Consider pooling records (later, not now).** ADR 004 treats this as an
advanced optimization.

---

## 11. Regression Policy

### 11.1 Per-category thresholds

| Category                          | Threshold |
|:---|:---|
| Disabled hot path                 | Fail on >3% if statistically significant |
| Enabled null handler              | Fail on >5% regression                  |
| Caller latency, queue file/socket | Fail on >5% p50 or >10% p99 regression  |
| End-to-end throughput             | Fail on >5% regression                  |
| Memory peak                       | Fail on >10% growth unless justified    |
| Drops in non-saturation tests     | Always fail                             |
| Correctness mismatch              | Always fail                             |
| Configuration benchmarks          | Warn at >10%, fail at >20%              |

### 11.2 Baseline and rolling updates

1. **Establish baseline** on pinned hardware (minimal load) for each benchmark
group.
2. **Store baseline JSON** in version control (e.g.,
`results/baseline-2026-06.json`).
3. **On each PR**, run smoke suite against baseline; report relative change %.
4. **Quarterly,** re-baseline on fresh hardware to account for system changes.

### 11.3 CI integration

1. Run smoke suite (`bench-smoke`) on every commit.
2. Run full suite (`bench-python`) on tagged releases or scheduled canary runs.
3. Fail PR if any metric exceeds threshold.
4. Comment on PR with comparison table and relative changes.

---

## 12. Reporting Format

### 12.1 Artefacts

Each benchmark run produces:

```python
results/<date>/<commit>/
├── raw.pyperf.json              # pyperf native output
├── criterion/                   # Criterion HTML reports (if run)
├── summary.json                 # Canonical metrics schema
├── summary.md                   # Markdown summary table
└── metadata.json                # System configuration, commit info
```python

### 12.2 Summary table format

```markdown
| Benchmark case          | Framework    | Mode   | Caller latency (µs) | E2E latency (µs) | Memory (MiB) | Drops |
|:---|:---|:---|:---|:---|:---|:---|
| enabled file tmpfs      | stdlib       | sync   | 0.8                 | 2.1              | 5            | 0     |
| enabled file tmpfs      | stdlib       | queue  | 0.15                | 1.9              | 8            | 0     |
| enabled file tmpfs      | loguru       | enqueue=False | 0.9          | 2.3              | 6            | 0     |
| enabled file tmpfs      | loguru       | enqueue=True  | 0.12         | 1.8              | 9            | 0     |
| enabled file tmpfs      | femtologging | async  | 0.08                | 1.7              | 7            | 0     |
```python

### 12.3 Benchmark meaning primer

Include a section explaining what each leaderboard measures and when to trust
it:

```markdown
## Understanding the benchmark results

**Caller latency:** Measures time spent in the logging call on the producer
thread. Lower is better for soft real-time applications. Femtologging's
queue-based design should show an advantage here compared with synchronous
handlers. When comparing queue-based frameworks (stdlib QueueHandler, loguru
enqueue=True, femtologging), caller latency dominates the user's experience.

**End-to-end latency:** Measures wall-clock time from the first log call to
final flush and output. This prevents asynchronous libraries from winning by
hiding work in background threads. A queue-based framework with slow consumers
may show poor end-to-end times despite fast caller latency.

**Memory:** Peak RSS (resident set size). Unbounded growth during benchmarks
indicates a leak or inefficient buffering.

**Drops:** Records lost due to queue overflow. Must be zero except in
explicitly-tested saturation scenarios.
```python

---

## 13. V0 Implementation Plan

### 13.1 Narrow v0 scope

Start with benchmarks that validate methodology without overwhelming
implementation:

```python
Disabled hot path:
  - disabled_literal (1 thread)
  
Enabled no-output:
  - enabled_literal_null (1 thread, 8 threads)
  
File I/O:
  - file_1_thread_sync_vs_queue
  - file_8_threads_sync_vs_queue
  - file_32_threads_sync_vs_queue
  
Saturation:
  - burst_then_flush
  - slow_consumer_saturation
  
Configuration:
  - basicConfig, dictConfig, builder
```python

### 13.2 V0 comparison targets

Focus on canonical cases; optional frameworks defer:

- stdlib.logging (sync)
- stdlib.logging (QueueHandler/QueueListener)
- picologging (sync)
- loguru (enqueue=False)
- loguru (enqueue=True)
- femtologging (default)

Add picologging queue mode and femtologging variants post-v0.

### 13.3 V0 success criteria

- All smoke benchmarks execute without error.
- Results export as JSON and markdown.
- Correctness verification passes (record counts, no drops, no malformed output).
- Comparison table shows femtologging's caller-latency advantage.
- Regression gate successfully blocks a simulated >threshold regression.

### 13.4 Phased expansion roadmap

- **v0:** Smoke suite (narrow scope, prove methodology).
- **v1:** Full concurrency matrix, file variants, saturation.
- **v2:** Socket handler, structured fields, advanced diagnostics.
- **v3:** Rust internals lab (Criterion microbenchmarks), optimization loop.

---

## 14. Appendices

### 14.1 Glossary

- **Caller latency:** Time spent in the logging call on the producer thread.
- **End-to-end latency:** Wall-clock time from first log call to final output.
- **Drain:** Flushing all queued records and confirming they've reached the sink.
- **Flush:** Writing buffered data to the sink (file, socket, etc.).
- **Handler:** The object responsible for formatting and outputting log records.
- **Producer thread:** Application thread calling the logging function.
- **Consumer thread:** Background thread (or Rust worker) processing queued records.
- **Overflow policy:** Behaviour when the queue is full (block or drop).
- **Saturation:** Queue depth reaching its configured capacity.

### 14.2 External artefact listings

Schemata, interface definitions, and architecture diagrams are defined in
separate files under `benchmarks/femtobench/` and referenced by the
implementation.

---

## Summary

This design document specifies:

1. **Three-leaderboard philosophy:** Caller latency, end-to-end completion,
diagnostic internals. Each leaderboard answers a different question and prevents
misleading comparisons.

2. **Fair comparison targets:** Frameworks and modes classified by
architecture, so queue-based libraries are compared fairly.

3. **Benchmark dimensions:** Comprehensive matrix covering framework, handler,
workload, concurrency, and tuning parameters.

4. **Metrics schema:** Canonical JSON structure for all results, enabling
statistical comparison and trend analysis.

5. **Adapter layer:** Clean interface for each framework, enforcing semantic
equivalence and honest representation.

6. **Measurement protocols:** pyperf for Python, Criterion for Rust, with
correctness verification and JSON export.

7. **Seven concrete benchmark groups:** Disabled hot path, enabled null, I/O,
batching, socket, saturation, configuration.

8. **Optimization playbook:** Bottleneck classification and targeted fixes for
future improvements.

9. **Regression policy:** Per-category thresholds and CI integration to prevent
performance degradation.

10. **V0 scope:** Narrow but meaningful starting suite, with clear expansion
roadmap.

Femtologging's queue-based architecture deserves an architecture-aware
benchmarking strategy. This design provides the evidence base for credible
public comparisons and guided internal optimizations.
