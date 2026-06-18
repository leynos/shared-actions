# RFC: Customizable Configuration Layering and File-Layer Resolution Policy

## Title

Customizable Configuration Layering and File-Layer Resolution Policy: Generic
Ordered Selectors and Multi-Scope Discovery for ortho-config

## Status

Draft

## Date

2026-06-18

## Summary

This RFC proposes adding a generic **file-layer resolution policy API** to
ortho-config, enabling applications like netsuke to express complex configuration
discovery patterns without implementing custom selection and merging logic. The
design provides three key primitives: (1) ordered explicit selector chains with
required/exclusive semantics, (2) multi-scope automatic discovery (system/user/
project), and (3) reusable file-layer resolution that separates layer discovery
from merge composition. This allows ortho-config to own the generic mechanics
while applications own policy choices (names, env-var spelling, scope ordering).

## Motivation

### Use Case: netsuke

netsuke maintains configuration discovery with a nuanced four-layer policy
currently implemented in local code (`src/cli/discovery.rs`):

1. **Explicit selection order**: `--config > NETSUKE_CONFIG > NETSUKE_CONFIG_PATH
   > automatic discovery`
2. **Fail-closed semantics**: Missing or malformed selected file stops discovery
   immediately (no fallback to automatic discovery)
3. **Project-over-user stacking**: Both user-scope and project-scope config load,
   with project config keys overriding user keys while preserving user-only keys
4. **Early resolution for diagnostic**: File layers must be available before full
   merge to extract `diag_json` and affect startup/merge error reporting

Today, netsuke cannot express these patterns using ortho-config's `compose_layers()`
API alone. Instead, netsuke must:

- Duplicate selector precedence logic in `collect_diag_file_layers(cli)`
- Manually orchestrate file loading and error handling
- Maintain custom path resolution that bypasses ortho-config's discovery
- Rebuild the selection chain during the merge pass, losing early diagnostics

### Broader Problem

Other applications with complex config discovery (Kubernetes, Docker Compose,
systemd, Poetry) all solve the same problem independently: expressing ordered
selection, multi-scope stacking, and explicit error handling without
reimplementing discovery mechanics. A standard library solution in ortho-config
would:

- Centralize selection logic at the point where policy is declared
- Enable testable, composable discovery pipelines
- Allow early access to config values (for diagnostics, feature gates)
- Support reuse of the same resolved layers across multiple merge contexts

### Why Existing Approaches Fall Short

**Current `compose_layers()` behavior**: Iterates a flat candidate list and
returns the first successful match. This model works for "pick one config file"
but cannot express:

- Explicit selection that excludes automatic fallback
- Multi-scope stacking where each scope contributes layers
- Semantic distinction between missing optional files (ignore) and missing
  required files (error)

---

## Proposed Solution

### Design Overview

The solution spans five staged deliverables:

1. **Runtime types**: Define `ConfigPathSelector`, `ExplicitMode`,
   `AutomaticDiscoveryMode`, `DiscoveryScope`, and `FileLayerOutcome`
2. **Scoped composition**: Add `ConfigDiscovery::compose_scoped_layers()` or
   `ConfigFilePolicy::resolve_layers()`
3. **Env-var selectors**: Support ordered explicit selectors with alias chains
4. **Merge-side integration**: Expose file layers as a reusable object for both
   early and late merging
5. **Macro extension**: Extend `discovery(...)` attributes to express the new
   capabilities

### Core Concepts

#### ConfigPathSelector

An explicit selector chain entry that can be CLI, environment variable, or
legacy env-var alias:

```rust
pub enum ConfigPathSelector {
    Cli(Option<PathBuf>),
    Env(String),
    EnvAlias {
        primary: String,
        aliases: Vec<String>,
        legacy: bool,
    },
}

impl ConfigPathSelector {
    pub fn cli(path: Option<PathBuf>) -> Self { ... }
    pub fn env(name: &str) -> Self { ... }
    pub fn env_alias(primary: &str) -> Self { ... }
    pub fn legacy_alias(mut self) -> Self { ... }
    pub fn label(&self, label: &str) -> Self { ... }
}
```

Semantics:
- **cli(path)**: If `Some(path)`, that path is required; if `None`, skipped
- **env(name)**: Check environment variable; if unset, try next selector
- **env_alias(primary)**: Check primary var; on miss, check aliases in order
- **legacy_alias()**: Mark this selector as deprecated (affects diagnostics)

#### ExplicitMode

Determines what happens when an explicit selector succeeds or fails:

```rust
pub enum ExplicitMode {
    Optional,           // If selector succeeds, use it; else try next
    RequiredExclusive,  // If selector succeeds, use it and stop
                        // If selector fails, report error and stop
}
```

**RequiredExclusive semantics**: Once an explicit selector matches (even to an
empty/missing file), automatic discovery is disabled.

#### AutomaticDiscoveryMode

Determines how automatic discovery composes layers:

```rust
pub enum AutomaticDiscoveryMode {
    FirstMatch,    // Current compose_layers() behavior
    StackScopes,   // Load from each scope, preserve order
}
```

#### DiscoveryScope

Identifies a tier in the discovery hierarchy:

```rust
pub enum DiscoveryScope {
    System,
    User,
    Project,
}
```

#### FileLayerOutcome

Distinguishes success/failure modalities:

```rust
pub struct FileLayerOutcome {
    pub layers: Vec<MergeLayer>,
    pub selected_path: Option<PathBuf>,
    pub discovery_exhausted: bool,
    pub errors: Vec<FileDiscoveryError>,
}

pub enum FileDiscoveryError {
    SelectedPathMissing { path: PathBuf },
    SelectedPathMalformed { path: PathBuf, reason: String },
    SelectedPathAccessDenied { path: PathBuf },
    DiscoveredPathMalformed { path: PathBuf, scope: DiscoveryScope },
    // ... other variants
}
```

### API Sketch

#### Builder-Based Discovery

```rust
let discovery = ConfigDiscovery::builder("netsuke")
    .explicit_selectors([
        ConfigPathSelector::cli(cli.config.clone()).label("--config"),
        ConfigPathSelector::env("NETSUKE_CONFIG"),
        ConfigPathSelector::env_alias("NETSUKE_CONFIG_PATH")
            .legacy_alias(),
    ])
    .explicit_mode(ExplicitMode::RequiredExclusive)
    .automatic_mode(AutomaticDiscoveryMode::StackScopes)
    .scope_order([
        DiscoveryScope::System,
        DiscoveryScope::User,
        DiscoveryScope::Project,
    ])
    .project_roots(project_roots)
    .project_file_name(".netsuke.toml")
    .build();

let file_plan = discovery.resolve_layers()?;

// Early access for diag_json
let diag_json = file_plan
    .merged_file_value()
    .and_then(|v| v.get("diag_json").and_then(Value::as_bool))
    .unwrap_or(default_diag_json);

// Later: push into composer for full merge
file_plan.push_into(&mut composer);
```

#### Derive Macro Extension

```rust
#[ortho_config(
    discovery(
        app_name = "netsuke",
        config_cli_long = "config",
        config_cli_visible = true,
        env_vars = ["NETSUKE_CONFIG", "NETSUKE_CONFIG_PATH"],
        explicit_mode = "required_exclusive",
        automatic_mode = "stack_scopes",
        project_file_name = ".netsuke.toml",
        project_root_from = "directory",
    )
)]
pub struct Config {
    /// Directory to search for config; affects discovery
    #[serde(skip)]
    pub directory: PathBuf,

    /// Early diagnostic merge flag
    pub diag_json: bool,

    // ... other fields
}
```

The macro generates:
- CLI field for `--config`/`-c` with appropriate visibility
- Static discovery configuration
- Hooks to pass `--directory` to the discovery API

### Merge-Side Integration

The resolved file plan exposes layers for flexible merge strategies:

```rust
pub struct FileLayerPlan {
    pub layers: Vec<(PathBuf, MergeLayer)>,
    pub outcome: FileLayerOutcome,
}

impl FileLayerPlan {
    pub fn merged_file_value(&self) -> Option<&Value> { ... }
    pub fn push_into(&self, composer: &mut MergeComposer) { ... }
    pub fn layers_for_scope(&self, scope: DiscoveryScope)
        -> impl Iterator<Item = &MergeLayer> { ... }
    pub fn success(&self) -> Result<(), FileDiscoveryError> { ... }
}
```

### Error Semantics

Resolution distinguishes four error modalities:

1. **Selected explicit path failed**: Do not use automatic discovery; report
   selected-file error as terminal
2. **Automatic optional probe failed**: Ignore unless discovery exhausts all
   candidates without match
3. **Present automatic file failed to parse**: Always report; presence implies
   intent
4. **Loaded file chain succeeded**: Preserve paths and layer order; merge errors
   happen downstream

Example: netsuke with `--config /etc/missing.toml`:

```
Error: Configuration file not found
Selected file: /etc/missing.toml (--config flag)

Suggestion: Check that the file exists and is readable, or omit --config to
            use default discovery paths.
```

---

## Detailed Design

### Stage 1: Runtime Types

Add to ortho-config crate:

- `ConfigPathSelector` enum and builder methods
- `ExplicitMode`, `AutomaticDiscoveryMode`, `DiscoveryScope` enums
- `FileLayerOutcome` and `FileDiscoveryError` types
- `FileLayerPlan` struct with query methods

**Tests**: Type construction, error construction, precedence semantics

### Stage 2: ConfigDiscovery Extension

Extend `ConfigDiscoveryBuilder`:

- Add `.explicit_selectors(Vec<ConfigPathSelector>)` method
- Add `.explicit_mode(ExplicitMode)` method
- Add `.automatic_mode(AutomaticDiscoveryMode)` method
- Add `.scope_order(Vec<DiscoveryScope>)` method
- Add `.project_roots(Vec<PathBuf>)` method
- Add `.project_file_name(String)` method
- Add `resolve_layers()` -> Result<FileLayerPlan> method

**Algorithm**:
1. Iterate explicit selectors in order; stop on match or error
2. If no explicit match, run automatic discovery per scope
3. For each scope, search candidate files; collect as separate layers
4. Return plan with all layers, outcome, and error detail

**Tests**:
- Explicit selector wins; stops discovery
- Explicit selector fail-closed behavior
- Automatic multi-scope stacking
- Project-over-user ordering
- Env-var alias chains
- Missing optional automatic files (no error)
- Invalid automatic files in participating scopes (error)

### Stage 3: Env-Var Alias Chains

Extend selectors to support alias fallback:

```rust
pub enum ConfigPathSelector {
    // ... existing variants ...
    EnvAliasChain {
        selectors: Vec<String>,
        legacy_mark: bool,
    },
}

impl ConfigPathSelector {
    pub fn env_chain(names: Vec<&str>) -> Self { ... }
}
```

**Resolution**: Try each env-var in order; use first non-empty value.

**Tests**:
- Canonical env-var checked first
- Aliases checked in order
- Empty values skipped
- Legacy marking affects error messages

### Stage 4: File-Layer Resolver

Create a public type that can be resolved multiple times:

```rust
pub struct ConfigFilePolicy {
    selectors: Vec<ConfigPathSelector>,
    explicit_mode: ExplicitMode,
    auto_mode: AutomaticDiscoveryMode,
    // ... other config ...
}

impl ConfigFilePolicy {
    pub fn from_cli(cli: &CliArgs) -> Self { ... }
    pub fn resolve_layers(&self) -> Result<FileLayerPlan> { ... }
}
```

Use case:
```rust
let policy = ConfigFilePolicy::from_cli(&cli);
let layers = policy.resolve_layers()?;  // For diag_json
let value = layers.merged_file_value();
// ... later ...
layers.push_into(&mut composer);        // For full merge
```

**Tests**: Early resolution followed by merge-side push produces identical
outcome to single resolution.

### Stage 5: Derive Macro Support

Extend the macro to generate discovery builder from attributes:

```rust
#[ortho_config(
    discovery(
        app_name = "app",
        env_vars = ["APP_CONFIG", "APP_CONFIG_PATH"],
        explicit_mode = "required_exclusive",
        automatic_mode = "stack_scopes",
    )
)]
```

Generate:
- Static discovery configuration
- CLI field (--config)
- Builder code that wires everything together

**Tests**: Generated code matches hand-written builder API.

---

## Testing Strategy

### Unit Tests per Stage

**Stage 1 types**:
- Selector construction and labeling
- Mode enums construction
- Error variant construction

**Stage 2 discovery**:
- Explicit selector matches (first wins)
- Explicit selector missing (fail-closed)
- Automatic discovery: first-match mode
- Automatic discovery: stack-scopes mode
- Scope ordering preserved in output layers
- Project-over-user key override semantics
- Env-var aliases in selectors
- `extends` chains across scopes maintained
- Early `merged_file_value()` matches final merge

**Stage 3 alias chains**:
- Env alias chain tries in order
- Canonical checked before aliases
- Empty values skipped
- Legacy marking visible in errors

**Stage 4 file-layer resolver**:
- `from_cli()` wires --config flag
- `resolve_layers()` idempotent
- Early and late resolution produce same layers
- `push_into()` places layers in correct order

**Stage 5 macro**:
- Generated code type-checks
- Generated CLI field correct type
- Generated builder matches hand-written

### Integration Tests

- **netsuke scenario**: --config > env > automatic; fail-closed on explicit
- **User + project**: user keys overridden by project; user-only keys preserved
- **Early diagnostic**: diag_json extracted before full merge
- **Error reporting**: Selected file vs. auto file errors distinct

---

## Migration Path

### Phase 1: Runtime Types and Core API (Week 1)

Implement stages 1–2. Add gated feature `config-file-policy` if needed.

### Phase 2: netsuke Integration (Week 2–3)

netsuke adopts `ConfigFilePolicy::from_cli()` for both early and late
resolution:

**Before**:
```rust
let diag_layers = collect_diag_file_layers(cli);
let diag_json = extract_diag_json(&diag_layers)?;
let mut composer = MergeComposer::new();
push_file_layers(cli, &mut composer, errors)?;
```

**After**:
```rust
let policy = ConfigFilePolicy::from_cli(&cli);
let layers = policy.resolve_layers()?;
let diag_json = layers.merged_file_value()
    .and_then(|v| v.get("diag_json").and_then(Value::as_bool))
    .unwrap_or(default);
let mut composer = MergeComposer::new();
layers.push_into(&mut composer);
```

### Phase 3: Macro Extension (Week 4)

Implement stage 5. Netsuke applies new macro attributes; tests verify identical
behavior to hand-written discovery code.

### Phase 4: Stabilization and Documentation

Write guides for custom discovery policies; document scope ordering contracts.

---

## Alternatives Considered

### Alternative 1: Fixed Scopes in ortho-config

**Approach**: Hardcode system/user/project scopes as ortho-config's only
discovery mode.

**Pros**: Simpler API surface

**Cons**:
- Applications with different scope hierarchies (Kubernetes uses namespace,
  cluster, global) cannot express their policy
- Violates principle that ortho-config owns mechanics, applications own policy

### Alternative 2: Early vs. Late Resolution as Separate Paths

**Approach**: Keep separate APIs for early (diagnostic) and late (merge) file
resolution.

**Pros**: Cleaner separation of concerns

**Cons**:
- Duplicate logic and testing
- Risk of divergence between early/late paths
- netsuke must maintain two selectors chains

### Alternative 3: Macro-Only Discovery

**Approach**: Put all discovery policy in macro attributes; no runtime builder
API.

**Pros**: Compile-time validation of policy

**Cons**:
- Cannot support policies that depend on runtime values (CLI --directory)
- Forces static configuration; no flexibility for conditional discovery
- netsuke's `--directory` affects discovery (hard to express in macro alone)

---

## Drawbacks

### Complexity of Multi-Scope Model

Applications must understand scope ordering and merge semantics. Mitigation:
provide clear defaults (System < User < Project) and write comprehensive guides.

### Potential for Scope Confusion

Nested scopes (e.g., User contains Workspace) require careful definition.
Mitigation: define scopes as an open enum; applications can extend with custom
types if needed.

### Early Merge Synchronization

The guarantee that early `merged_file_value()` matches final merge must hold
even with `extends` chains. Mitigation: comprehensive integration tests.

---

## Unresolved Questions

### 1. Custom Scopes

Should applications be able to define their own scopes (e.g., Workspace,
Cluster)?

- **Current proposal**: Open enum; applications define variants as needed
- **Alternative**: Closed set of scopes; custom needs must map to existing ones

### 2. Scope Aliases

Should netsuke's `.netsuke.toml` in user directory be called "User" or
"Project"?

- **Current proposal**: Application names their scopes; "User" and "Project" are
  suggestions
- **Alternative**: Standardize names across ecosystem

### 3. Extends Precedence Across Scopes

If User config extends Base, and Project config extends User, what merge order
applies?

- **Current proposal**: All extends chains load as separate layers; final order
  is User-scope < Project-scope
- **Alternative**: Extends chains respect cross-scope order; Project-scope
  extends can reference User values

### 4. Performance Profiling

How much overhead does multi-scope discovery add versus first-match? Should
there be an early-exit optimization if Project scope finds a match?

### 5. Error Recovery

If a Project-scope file is malformed, should discovery fall back to User-only
config?

- **Current proposal**: No; malformed present files always error
- **Alternative**: Optional fallback mode for graceful degradation

---

## Next Steps

1. **Design review**: Gather feedback from netsuke team and community
2. **Prototype**: Implement stages 1–2 as a proof-of-concept
3. **netsuke trial**: Apply to netsuke; verify fail-closed and early-diag
   semantics
4. **Testing**: Comprehensive test matrix for all scenarios
5. **Macro design**: Refine `discovery(...)` attribute syntax
6. **Release**: Ship with documentation and migration guide

---

## References

- [Netsuke config discovery](file:///tmp/lody-title-agent/src/cli/discovery.rs)
- [ortho-config ConfigDiscovery](
  file:///tmp/lody-title-agent) — baseline API
- [RFC 0001: Environment Variable Aliases](rfc-ortho-config-env-aliases.md) —
  Related feature for alias chains
- [Kubernetes ConfigMap
  composition](https://kubernetes.io/docs/concepts/configuration/configmap/) —
  Multi-scope precedence example
- [Docker Compose environment file
  precedence](https://docs.docker.com/compose/env-file/) — Real-world stacking
  example
- [systemd EnvironmentFiles](https://www.freedesktop.org/software/systemd/man/systemd.exec.html#EnvironmentFiles=)
  — Ordered file merging pattern
