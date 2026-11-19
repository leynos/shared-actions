# Migrating to registry image refresh in `validate-linux-packages`

This guide covers the `refresh-base-images` input added to the
`validate-linux-packages` action. Its default changes the action's behaviour
for existing callers: each base image is now pulled from its registry before
Polythene exports it.

## What changed, and why

Before this change the action exported whichever base image Podman already
held. On a persistent runner that could be an image from an earlier run, so a
validation could pass against an image the registry had since replaced. The
action now runs `podman pull` for `deb-base-image` and `rpm-base-image` first,
so each validation uses the image the tag currently names.

## When to migrate

Nothing is required for a caller that uses registry images and accepts one pull
per validation run. Review the change when a job:

- uses a base image that exists only in the local Podman store;
- runs without network access; or
- pins an image tag that the registry no longer serves.

## How to migrate

Keep the default for registry-backed images. To opt out, set
`refresh-base-images: false` and, when the image is local-only, override the
image the sandbox uses:

```yaml
- uses: leynos/shared-actions/.github/actions/validate-linux-packages@<sha>
  with:
    bin-name: rust-toy-app
    version: 1.2.3
    deb-base-image: localhost/debian:bookworm
    refresh-base-images: false
```

`refresh-base-images` must be `true` or `false`; any other value fails the run
with a message naming the input. The value is trimmed and case-folded, so
`TRUE` and `False` are accepted.

## `polythene` versus `polythene-isolate`

This change does not change how the action finds the `polythene` executable. It
runs the `polythene` on `PATH`, or the script named by `polythene-path`, and it
does not install `polythene-isolate`. The `polythene-isolate` distribution is
pinned in this repository's `pyproject.toml` for local tooling and tests.

## Rolling back

Set `refresh-base-images: false` to stop pulling. The action then uses the
image Podman already holds, as it did before this change.
