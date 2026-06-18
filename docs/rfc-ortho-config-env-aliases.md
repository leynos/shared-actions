# RFC: Environment Variable Aliases for ortho-config

## Title

Environment Variable Aliases for ortho-config: Support Dynamic Aliasing and
Precedence-Based Fallback

## Status

Draft

## Date

2026-06-18

## Summary

This RFC proposes adding support for environment variable aliases in
ortho-config, allowing multiple names to resolve to the same configuration
parameter. The design supports both compile-time alias declaration (via
procedural derive macros) and runtime alias registration, with a clear
precedence model that respects canonical names over aliases. This feature
enables smoother migration for projects like vk and podbot that need to support
legacy variable names while standardizing on canonical ones.

## Motivation

### Use Case: vk

The vk project maintains configuration across multiple deployment environments.
As vk evolves, environment variable naming conventions may change or
consolidate. For example:

- Old convention: `VK_DATABASE_HOST`, `VK_DB_HOST` (inconsistent naming)
- New convention: `VK_DATABASE_HOST` (single canonical name)

Without alias support, vk must either:

1. Hardcode checks for multiple variable names throughout the codebase,
creating maintenance burden
2. Perform manual pre-processing of environment variables at startup
3. Maintain two parallel configuration systems indefinitely

With aliases, vk can declare `VK_DB_HOST` as an alias for
`VK_DATABASE_HOST`, automatically falling back to the old name when the
canonical name is unset. This enables gradual migration without code
duplication.

### Use Case: podbot

Podbot integrates ortho-config for multi-tenant audio processing configuration.
As the platform matures:

- Original var names used underscores: `PODBOT_AUDIO_SAMPLE_RATE_HZ`
- Standardization effort switches to kebab-case in configuration: `podbot.audio.sample-rate-hz`
- Internal library names differ from external configuration names

Aliases allow podbot to:

1. Define the production canonical name in configuration
2. Declare aliases for legacy environment variables, documentation names, and
integration points
3. Support multiple deployment scenarios (legacy CI/CD, containers, Kubernetes)
without code changes

### Core Problem

Both projects face this pattern: environment-first systems need to support
**backward compatibility** while **migrating to new naming schemes**. Current
solutions are ad-hoc and spread throughout application code. A standardized
mechanism in ortho-config would:

- Centralize alias definitions at the configuration struct definition site
- Enable zero-cost aliases (checked at compile time where possible)
- Provide clear semantics for precedence and fallback behavior
- Reduce maintenance burden and human error

## Proposed Solution

### Derive Macro Syntax

```rust
use ortho_config::Config;

#[derive(Config)]
#[ortho_config(env_prefix = "VK_")]
struct DbConfig {
    /// Primary database hostname
    #[ortho_config(
        env_name = "DATABASE_HOST",
        aliases = ["DB_HOST", "DBHOST"]
    )]
    database_host: String,

    /// Connection pool size; legacy var name still accepted
    #[ortho_config(
        env_name = "POOL_SIZE",
        aliases = ["CONNECTION_POOL_SIZE"],
        default = "10"
    )]
    pool_size: u32,
}
```

Expansion semantics:

- `DATABASE_HOST` (canonical) is checked first
- If unset, `DB_HOST` is checked
- If unset, `DBHOST` is checked
- If still unset, the default is used

### Runtime API

For dynamic alias registration:

```rust
use ortho_config::{Config, Aliases};

// Register aliases at startup
let aliases = Aliases::new()
    .alias("PODBOT_AUDIO_SR", "PODBOT_AUDIO_SAMPLE_RATE_HZ")?
    .alias("LEGACY_MODE", "PODBOT_MODE")?;

// When creating config, provide aliases
let config = PodBotConfig::from_env_with_aliases(&aliases)?;

// Or use a builder pattern
let config = PodBotConfig::builder()
    .with_aliases(aliases)
    .from_env()?;
```

### Precedence Rules

Aliases follow a strict precedence hierarchy:

1. **Canonical environment variable** (highest priority)
2. **Aliases in declaration order** (left to right as listed in `aliases = [...]`)
3. **Default value** (if provided)
4. **Error** (if no default and no env vars set)

Example walkthrough:

```rust
#[ortho_config(env_name = "FOO", aliases = ["BAR", "BAZ"])]
field: String,
```

Resolution with environment state:

| State                      | Resolved Value | Source                |
| -------------------------- | -------------- | --------------------- |
| `FOO=a, BAR=b, BAZ=c`      | `a`            | Canonical (highest)   |
| `BAR=b, BAZ=c`             | `b`            | First alias           |
| `BAZ=c`                    | `c`            | Second alias          |
| `(none)` + `default="d"`   | `d`            | Default value         |
| `(none)` + no default      | Error          | No source found       |

## Detailed Design

### Macro Levels

#### Compile-Time Macro Expansion

The `#[ortho_config(...)]` attribute macro generates code that:

1. **Statically generates the resolution chain** during compilation
2. **Unrolls the precedence checks** into individual environment lookups
3. **Emits constants** listing all possible names for documentation/debugging

Example generated code:

```rust
// Original:
#[ortho_config(env_name = "DATABASE_HOST", aliases = ["DB_HOST"])]
database_host: String,

// Generates approximately:
const DATABASE_HOST_NAMES: &[&str] = &["DATABASE_HOST", "DB_HOST"];

// Within from_env() expansion:
let database_host = env::var("VK_DATABASE_HOST")
    .or_else(|_| env::var("VK_DB_HOST"))
    .context("DATABASE_HOST configuration")?;
```

#### Runtime Registration

For scenarios where aliases cannot be known at compile time, a
`#[ortho_config(runtime_aliases)]` mode allows registration:

```rust
#[derive(Config)]
#[ortho_config(env_prefix = "APP_", runtime_aliases = true)]
struct Config {
    #[ortho_config(env_name = "MODE")]
    mode: String,
}

// At startup:
let mut aliases = Aliases::builder();
if uses_legacy_system() {
    aliases.alias("LEGACY_MODE", "APP_MODE")?;
}
let config = Config::from_env_with_aliases(aliases.build())?;
```

### Runtime API Details

#### Alias Builder

```rust
pub struct AliasesBuilder { /* ... */ }

impl AliasesBuilder {
    /// Register that `alias_name` is an alias for `canonical_name`
    /// Both names are tested; canonical is tried first
    pub fn alias(
        mut self,
        alias_name: &str,
        canonical_name: &str,
    ) -> Result<Self> {
        // Validation: neither can be empty
        // Validation: alias != canonical
        // Validation: no cycles (A->B, B->A)
        Ok(self)
    }

    /// Build the immutable Aliases struct
    pub fn build(self) -> Aliases {
        Aliases { /* ... */ }
    }
}

pub struct Aliases {
    // Internal: map of alias -> canonical
    map: HashMap<String, String>,
}

impl Aliases {
    pub fn resolve(&self, name: &str) -> &str {
        // If name is an alias, return canonical name
        // Otherwise, return name unchanged
        self.map.get(name).map(|s| s.as_str()).unwrap_or(name)
    }

    pub fn all_names(&self, canonical: &str) -> Vec<&str> {
        // Return canonical and all aliases pointing to it
    }
}
```

#### Integration with Config Trait

```rust
pub trait Config: Sized {
    /// Standard from_env() - uses only compile-time aliases
    fn from_env() -> Result<Self>;

    /// With runtime aliases
    fn from_env_with_aliases(aliases: &Aliases) -> Result<Self>;

    /// Merge aliases into resolution chain
    fn resolve_env_var(name: &str, aliases: &Aliases) -> Result<String>;
}
```

### Detailed Resolution Algorithm

The resolution engine applies aliases at multiple levels:

1. **Field-level resolution** (within a struct field)
2. **Nested struct resolution** (for composite configs)
3. **Array/list resolution** (for repeated config blocks)

For each field:

```rust
fn resolve_field(field_name, field_spec, aliases):
    // field_spec = {env_name, aliases, default, ...}

    canonical = env_prefix + field_spec.env_name
    candidates = [canonical] + [env_prefix + a for a in field_spec.aliases]

    for candidate in candidates:
        if runtime_aliases provided:
            candidate = runtime_aliases.resolve(candidate)
        if env_var_exists(candidate):
            return parse(env_var(candidate))

    if field_spec.default:
        return field_spec.default
    else:
        return Error
```

### Testing Strategy

#### Unit Tests

```rust
#[cfg(test)]
mod tests {
    use ortho_config::*;

    #[test]
    fn test_canonical_takes_precedence() {
        // Set both canonical and alias
        // Verify canonical is used
    }

    #[test]
    fn test_alias_used_when_canonical_absent() {
        // Set only first alias
        // Verify it's used
    }

    #[test]
    fn test_alias_order_respected() {
        // Set second alias but not first
        // Verify second alias is used
    }

    #[test]
    fn test_default_used_when_all_absent() {
        // Don't set any env vars
        // Verify default is used
    }

    #[test]
    fn test_error_when_required_absent() {
        // Don't set any env vars, no default
        // Verify error is returned with helpful message
    }

    #[test]
    fn test_cycle_detection() {
        // Try to register A->B, B->A
        // Verify error during build
    }

    #[test]
    fn test_nested_struct_aliases() {
        // Config with nested struct containing aliases
        // Verify aliases work transitively
    }

    #[test]
    fn test_runtime_override_of_compile_time_aliases() {
        // Register runtime alias that shadows compile-time
        // Verify correct precedence
    }
}
```

#### Integration Tests

```rust
#[cfg(test)]
mod integration_tests {
    #[test]
    fn test_vk_migration_path() {
        // Simulate vk migration from VK_DB_HOST to VK_DATABASE_HOST
        // Verify old vars still work in backward-compat mode
        // Verify new vars take precedence
    }

    #[test]
    fn test_podbot_multi_naming_scenario() {
        // Simulate podbot with legacy, current, and future naming
        // Verify all resolve correctly with right precedence
    }
}
```

#### Documentation Generation

Aliases are included in generated documentation:

```rust
#[derive(Config)]
struct MyConfig {
    #[ortho_config(env_name = "FOO", aliases = ["BAR", "BAZ"])]
    /// Main configuration
    field: String,
}

// Auto-generates:
// - docs/generated/config-reference.md listing all names per field
// - config help message showing acceptable names
// - migration guide for deprecated aliases
```

### Error Messages

When configuration fails, errors clearly indicate all names tried:

```text
Error: failed to load configuration field 'database_host'

Tried in order:

  1. VK_DATABASE_HOST (canonical)
  2. VK_DB_HOST (alias)
  3. VK_DBHOST (alias)

No value found and no default set.

Suggestion: Set one of the above environment variables.

Learn more:
[https://docs.example.com/vk/config#database_host](https://docs.example.com/vk/config#database_host)
```

## Migration Path

### Phase 1: vk Migration (Month 1)

1. **Tag canonical variables** in vk's current code
   - Define `VK_DATABASE_HOST` as the canonical name
   - List all legacy names used historically

2. **Declare aliases** in config structs

   ```rust
   #[ortho_config(env_name = "DATABASE_HOST", aliases = ["DB_HOST", "DBHOST"])]
   database_host: String,
   ```

3. **Test backward compatibility**
   - Run with old env var names only
   - Verify old scripts still work

4. **Document migration**
   - Release notes: "Both `VK_DB_HOST` and new `VK_DATABASE_HOST` work"
   - Deprecation timeline: "Legacy names supported through vX.Y"

### Phase 2: podbot Migration (Month 2)

1. **Inventory naming inconsistencies**
   - Old: `PODBOT_AUDIO_SAMPLE_RATE_HZ`
   - New canonical: `PODBOT_AUDIO_SAMPLE_RATE_HZ` (standardize)
   - External/legacy: `PODBOT_AUDIO_SR`, `LEGACY_MODE`, etc.

2. **Implement runtime alias registration**

   ```rust
   let mut aliases = Aliases::builder();

   // Legacy external integrations
   aliases.alias("PODBOT_AUDIO_SR", "PODBOT_AUDIO_SAMPLE_RATE_HZ")?;
   aliases.alias("LEGACY_MODE", "PODBOT_MODE")?;

   // Container/Kubernetes secrets using different scheme
   if is_container_env() {
       aliases.alias("AUDIO_RATE", "PODBOT_AUDIO_SAMPLE_RATE_HZ")?;
   }

   let config = PodBotConfig::from_env_with_aliases(aliases.build())?;
   ```

3. **Gradual deployment**
   - Stage 1: Support both old and new in staging
   - Stage 2: Require new names in production, warn on old ones
   - Stage 3: Remove old names after grace period

### Phase 3: Community Adoption

vk and podbot publish:

- Migration guides
- Before/after comparisons
- Case studies showing maintenance reduction

Other projects adopt aliases for their own configurations.

## Alternatives Considered

### Alternative 1: Raw Environment Scanning at Startup

**Approach**: Projects scan for multiple variable names and rename them before
initialization.

```rust
// Anti-pattern: scattered throughout codebase
fn load_config() -> Result<Config> {
    // Manual renaming
    if let Ok(v) = env::var("VK_DB_HOST") {
        env::set_var("VK_DATABASE_HOST", v);
    }
    if let Ok(v) = env::var("DBHOST") {
        env::set_var("VK_DATABASE_HOST", v);
    }
    Config::from_env()
}
```

**Pros**:

- No framework changes needed

**Cons**:

- Duplicated across projects
- Error-prone (easy to miss names)
- Hidden logic outside config struct definition
- Hard to document
- Can't optimize (multiple env lookups)
- Imperative, not declarative

### Alternative 2: Separate Configuration Layer

**Approach**: Add a "pre-processing" layer that normalizes environment before
passing to ortho-config.

```rust
struct LegacyEnvMapper {
    mappings: HashMap<&str, &str>,
}

impl LegacyEnvMapper {
    fn apply(&self) {
        for (old, new) in &self.mappings {
            if let Ok(v) = env::var(old) {
                env::set_var(new, v);
            }
        }
    }
}
```

**Pros**:

- Loosely coupled to ortho-config
- Could be reused in other contexts

**Cons**:

- Requires explicit invocation before `from_env()`
- Easy to forget or misconfigure
- Still duplicates logic across projects
- Less efficient (double environment reads)
- Doesn't provide documentation benefits

### Alternative 3: Environment Variable Configuration File

**Approach**: Use a dotenv-like file listing aliases.

```env
# aliases.env
ALIAS VK_DB_HOST -> VK_DATABASE_HOST
ALIAS VK_DBHOST -> VK_DATABASE_HOST
ALIAS PODBOT_AUDIO_SR -> PODBOT_AUDIO_SAMPLE_RATE_HZ
```

**Pros**:

- Centralized
- Can be version-controlled

**Cons**:

- Another file to manage and synchronize
- Breaks single source of truth (separate from config struct)
- Requires parsing at runtime for every lookup
- Harder to discover (hidden in file, not visible in code)
- No type safety or compile-time validation

### Why This RFC is Better

The proposed approach:

1. **Colocates** alias definitions with field definitions (single source of
truth)
2. **Provides** compile-time validation and optimization
3. **Supports** both compile-time and runtime aliases (flexible)
4. **Generates** documentation automatically
5. **Ensures** type safety through Rust's type system
6. **Reduces** boilerplate and human error

## Drawbacks

### Compilation Time Impact

The macro expansion adds non-trivial code to each field with aliases:

```rust
// Small for one alias, ~1 line of generated code per alias
// For 50-field config with ~2 aliases per field, adds ~100 lines
// Negligible impact on compile time, but worth benchmarking
```

**Mitigation**:

- Profile macro expansion
- Offer `lazy = true` option if needed (defer to runtime resolution)

### Documentation Burden

Projects must maintain alias lists in two places:

1. Source code (as attributes)
2. User documentation (manually)

**Mitigation**:

- Auto-generate `config-reference.md` from macro attributes
- Include generated docs in CI validation

### Potential for Misuse

Developers might declare excessive aliases, creating confusion:

```rust
#[ortho_config(
    env_name = "FOO",
    aliases = ["BAR", "BAZ", "QUX", "QUUX", "CORGE", "GRAULT"]  // Too many!
)]
field: String,
```

**Mitigation**:

- Lint warning when >3 aliases per field
- Documentation recommends max 2-3
- Migration guide emphasizes retiring old names

### Runtime Alias Registration Complexity

Runtime aliases add complexity to initialization:

```rust
// More verbose than simple from_env()
let config = Config::from_env_with_aliases(
    Aliases::builder()
        .alias("A", "B")?
        .alias("C", "D")?
        .build()
)?;
```

**Mitigation**:

- Provide builder method: `Config::with_runtime_aliases(|a| a.alias(...)?)`
- Document common patterns
- Examples for typical scenarios

## Unresolved Questions

### 1. Case Sensitivity

Should aliases be **case-sensitive**?

- **Current proposal**: Yes, case-sensitive (standard for Unix env vars)
- **Alternative**: Case-insensitive matching option

**Open**: Should we offer a `case_insensitive = true` attribute?

### 2. Deprecation Tracking

How should deprecated aliases be tracked over time?

```rust
#[ortho_config(
    env_name = "NEW_NAME",
    aliases = [
        ("OLD_NAME", "1.0", "Use NEW_NAME instead"),
    ]
)]
```

**Open**: Should we add deprecation metadata and emit warnings?

### 3. Environment Variable Precedence vs Aliases

When both runtime and compile-time aliases exist for the same field, what wins?

- **Current proposal**: Compile-time aliases tried first, then runtime aliases
- **Alternative**: Runtime aliases override compile-time

**Open**: Should precedence be customizable or fixed?

### 4. Array/List Handling

For repeated config elements (e.g., multiple database replicas):

```rust
#[ortho_config(
    env_name = "REPLICA_URLS",
    aliases = ["REPLICA_HOSTS", "DB_REPLICAS"]
)]
replicas: Vec<String>,
```

**Open**: Do aliases apply to the entire list, or individually?

### 5. Nested Struct Aliases

When a nested struct has its own aliases, and the parent also does:

```rust
#[derive(Config)]
struct Parent {
    #[ortho_config(aliases = ["OLD_DB"])]
    db: DbConfig,  // DbConfig itself has aliases on fields
}
```

**Open**: Should nested aliases be merged, stacked, or isolated?

### 6. Performance Profiling

Before stabilizing, we need to measure:

- Macro expansion overhead
- Runtime lookup performance vs. simple `env::var()`
- Memory overhead of Aliases struct

**Open**: What are acceptable performance targets?

## Next Steps

1. **Implementation phase**:
   - Write macro expansion code with comprehensive tests
   - Build Aliases runtime API
   - Integrate into ortho-config crate

2. **Review phase**:
   - Get feedback from vk and podbot teams
   - Performance profiling against targets
   - Test with real migration scenarios

3. **Documentation phase**:
   - Write user guide with examples
   - Create migration playbooks for vk and podbot
   - Generate reference documentation

4. **Stabilization**:
   - Gather feedback from early adopters
   - Resolve unresolved questions
   - Version bump and release

## References

- [12-Factor App: Config](https://12factor.net/config) — environment-first
  principle
- [Rust RFC 2407: Anonymous struct
  fields](https://rust-lang.github.io/rfcs/2407-anonymous-fields.html) —
  related feature design
- [dotenv-rs](https://github.com/dotenv-rs/dotenv-rs) — prior art in Rust env
  handling
- [Kubernetes environment variable
  naming](https://kubernetes.io/docs/tasks/configure-pod-container/define-environment-variable-container/)
  — real-world naming patterns
