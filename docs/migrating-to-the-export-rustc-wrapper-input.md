# Declining the job-wide sccache wrapper

This guide covers the `export-rustc-wrapper` input and the `sccache-path`
output of `setup-rust`, added in the next minor release. Nothing changes for a
caller that does not use them, and this guide says how to tell whether a job
should.

## What changed

`setup-rust` exports `RUSTC_WRAPPER`, naming sccache, to the whole job, so
every later step inherits it. Two kinds of step cannot use it:

- a root lane run through `sudo -E`, whose `rustc` fails with "Permission
  denied" against a server owned by the runner user, and
- nested cargo builds such as trybuild fixtures, which run many small compiles
  under the wrapper. On pg-embed-setup-unpriv #314 (run 36998858912) dropping
  the wrapper from the two test steps took the trybuild UI tests from timeouts
  over 360 s to 91-102 s (unprivileged lane) and 136-148 s (root lane), with
  799 of 799 tests passing.

The new input lets a job decline the export. The default is `'true'`, so
behaviour is unchanged until a caller sets it.

## When to migrate

No caller has to. Consider it for a job that:

- runs a test lane under `sudo -E`, or
- builds fixture crates through nested `cargo`, or
- has seen timeouts that disappear when the wrapper is unset.

## How to migrate

Set `export-rustc-wrapper: 'false'` on the `setup-rust` step and give the step
an `id`. The server still starts and `SCCACHE_PATH` stays exported. The new
`sccache-path` output names the binary once the server has started (it is empty
after a fallback, so gate any use of it on `sccache-status`). Wrap only the
commands that should use it:

```yaml
- id: setup
  uses: leynos/shared-actions/.github/actions/setup-rust@<sha>
  with:
    export-rustc-wrapper: 'false'
- name: Build
  run: RUSTC_WRAPPER="${{ steps.setup.outputs.sccache-path }}" cargo build
```

To decline for one step only, keep the default and set `RUSTC_WRAPPER: ''` in
that step's `env:` (an empty value counts as unset for Cargo). For a root lane,
put it inside the `sudo` command: `sudo -E env RUSTC_WRAPPER= make test`.

A `RUSTC_WRAPPER` the caller already set is never overridden, whichever way the
input is set.

## Rolling back

Remove the input, or set it back to `'true'`. Nothing else was written, so
there is nothing to undo.
