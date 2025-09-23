# Python Native Command Mocking Design

CmdMox underpins the Python-based command doubling strategy. The library offers
an ergonomic façade for writing tests while keeping the execution model explicit
and deterministic. This document captures the architectural decisions and the
contracts relied upon by the higher-level usage guide.

## Objectives

- Provide a transport-agnostic façade that lets tests intercept subprocess
  invocations without patching the Python standard library.
- Support mocks, stubs and spies with a consistent fluent DSL that emphasizes
  readability.
- Capture interactions for later inspection through a replay journal so tests
  remain debuggable.
- Remain portable across Unix platforms while documenting the Windows
  limitations of the IPC transport.

## Architecture Overview

CmdMox consists of three cooperating subsystems:

1. **Controller** – The public entry point used by tests. It configures
   expectations, manages lifecycle transitions and coordinates verification.
2. **Environment** – Provisions temporary shim binaries (or scripts) and binds
   them to the controller via Unix domain sockets. Environment configuration is
   exposed through attributes such as `environment.shim_dir`.
3. **IPC Server** – Handles requests from shims, dispatching them to the
   recorded doubles. The server enforces strict sequencing to maintain
   deterministic behaviour.

The pytest plugin creates a controller per test function. When used as a context
manager (`with CmdMox() as mox:`) the same controller lifecycle is available for
non-pytest clients.

## Lifecycle: Record → Replay → Verify

CmdMox enforces a three-stage lifecycle:

1. **Record** – Tests describe expectations using the fluent API. Each
   expectation registers a command double with information about argument
   matching, environment and the response strategy.
2. **Replay** – The controller activates the IPC server and replaces the target
   commands with shims. During this phase, invocations flow through the doubles.
3. **Verify** – Finally, the controller checks that every expectation was
   satisfied, including call counts and ordering rules.

Exiting a `with CmdMox()` block triggers `verify()` automatically when
`verify_on_exit=True` (the default). Failing verification suppresses the error
if an exception already bubbled out of the context, keeping the original
exception visible to the test runner.

## Command Doubles and Responses

`CommandDouble` instances configure behaviour with a fluent DSL:

- `with_args(*args)` asserts exact argument sequences.
- `with_matching_args(*matchers)` allows per-position comparator functions such
  as `Regex`, `Contains`, `StartsWith`, `Any`, `IsA` or custom predicates.
- `with_stdin(...)` and `with_env({...})` match stdin content and environment
  fragments.
- `returns(stdout="", stderr="", exit_code=0)` provides deterministic
  responses; the API operates exclusively on `str` payloads.
- `runs(handler)` executes dynamic hooks that receive an `Invocation` object.
- `times(count)` and `times_called(count)` enforce call counts, with the latter
  acting as a spy-specific alias.
- `passthrough()` forwards execution to the real command while continuing to
  record invocations.
- `assert_called*` helpers are available on spies after verification to ease
  assertions in tests.

## Journal and Diagnostics

Every invocation processed during replay is appended to `cmd_mox.journal`, a
bounded `collections.deque`. The capacity is controlled by
`max_journal_entries`; exceeding the limit evicts the oldest entries. The
journal is the primary diagnostic surface for understanding unexpected
interactions and is frequently asserted against in tests.

## Environment Variables

Two environment variables tie the controller and shims together:

- `CMOX_IPC_SOCKET` – Path to the Unix domain socket exposed by the server. Shims
  exit early if this variable is missing.
- `CMOX_IPC_TIMEOUT` – Seconds to wait for IPC operations before raising a
  timeout error. The default is `5.0` seconds and can be tuned per test via the
  controller API.

These variables are injected automatically when the pytest fixture or context
manager initialises the controller.

## Platform Notes

The IPC transport relies on Unix domain sockets, so the pytest plugin guards
against activation on Windows (`sys.platform == "win32"`). Tests should guard
Windows-specific code paths accordingly. Future work may explore TCP loopback or
named-pipe transports for full parity.

## Error Handling and Validation

- The controller refuses to enter replay without recorded expectations when
  strict verification is required, ensuring unexpected commands fail fast.
- Each shim invocation is validated against its matching strategy; mismatches are
  surfaced immediately with descriptive error messages.
- Journal eviction and verification are both deterministic so repeated runs yield
  identical behaviour given the same expectations and inputs.

CmdMox is designed to remain implementation-agnostic at the call site, allowing
maintainers to evolve the underlying IPC layer or shim mechanism without
breaking tests that depend on the documented contracts above.
