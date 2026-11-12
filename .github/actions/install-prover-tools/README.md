# Install prover tools

Install Kani, Verus and Z3 through `leynos/rust-prover-tools`, pinned by full
commit SHA, so a consumer keeps no installer script, no copied install step and
no cache plumbing of its own.

```yaml
- uses: leynos/shared-actions/.github/actions/install-prover-tools@<sha>
  with:
    tools: kani verus
    runner-image: ubuntu2404
```

The action installs and caches. It does not run proofs: a consumer's Makefile
keeps thin targets that call `prover-tools kani exec` or
`prover-tools verus run`, so the proof invocation stays beside the code it
proves.

## Inputs

| Input               | Default            | Meaning                                              |
| ------------------- | ------------------ | ---------------------------------------------------- |
| `tools`             | required           | Any of `kani`, `verus`, `z3`; `verus` adds `z3`.     |
| `prover-tools-sha`  | `592558f7…`        | Full 40-character SHA; a tag or branch is refused.   |
| `repo-root`         | `.`                | Where `tools/*/VERSION` are read and `.verus` lives. |
| `runner-image`      | required           | Image label, carried in the cache key.               |
| `kani-install-dir`  | `$CARGO_HOME/bin`  | Where `cargo-kani` and `kani` land; put on `PATH`.   |
| `verus-install-dir` | `.verus/<version>` | Verus release directory.                             |
| `z3-install-dir`    | `.verus/z3`        | Z3 binary directory.                                 |

A relative install directory is read from `repo-root`, as the tool reads it,
not from the job's working directory.

Outputs: `verus-bin`, `z3-path` (for `VERUS_Z3_PATH`), and `cache-hit`.

## Digest checks

The action restates no digest. `rust-prover-tools` pins each asset's SHA-256 at
the named revision and checks every download against it, with a byte ceiling
and a transfer deadline. A cached archive is hashed against that digest each
time it is used and replaced when it does not match, and binaries are always
extracted from an archive that passed. The workflow
`test-install-prover-tools.yml` poisons a cached archive and checks that the
next install repairs it.

Two limits come from the tool, not this action. Verus has no archive cache in
the tool, so its release downloads on every run (digest-checked). And
`prover-tools z3 install` reuses an installed solver on a marker alone, without
re-hashing the binary ([rust-prover-tools#245]); the action clears nothing
there, so a consumer that wants that guarantee should install into a fresh
directory.

## Cache

One entry per tool holds its verified archives:

```text
prover-tools-v1-<os>-<arch>-<environment>-<runner-image>-<tool>-<version>-<sha12>
```

`<version>` is the consumer's `tools/<tool>/VERSION`; Z3 has none, so the
revision prefix stands in. Every run restores; only a push to the default
branch saves, and only after an exact miss.

[rust-prover-tools#245]: https://github.com/leynos/rust-prover-tools/issues/245
