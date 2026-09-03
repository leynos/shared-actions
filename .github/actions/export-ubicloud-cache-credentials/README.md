# export-ubicloud-cache-credentials

Publish Ubicloud's local cache proxy credentials to the job environment, so
later shell steps can reach it.

> [!IMPORTANT]
> This action is for Ubicloud runners only. It fails closed on a
> GitHub-hosted runner rather than exporting that runner's cache endpoint
> under Ubicloud's name.

## Why it exists

A GitHub Actions runner exposes `ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN`
to **action steps only**. A `run:` step never sees them, so a shell step that
starts an sccache server has no way to learn where the cache lives. On
Ubicloud, `ACTIONS_CACHE_URL` points at a proxy on the runner's private
network, something like `http://10.1.2.3:51123/<token>/`, which stores objects
in Ubicloud's own cache rather than GitHub's.

This action reads those values where they are visible and republishes them
through `GITHUB_ENV`, where every later step can read them.

## Inputs

None.

## What it exports

<!-- markdownlint-disable MD013 -->
| Variable | Value |
| --- | --- |
| `ACTIONS_CACHE_URL` | The proxy URL, unchanged |
| `ACTIONS_RUNTIME_TOKEN` | The runtime token, unchanged |
| `ACTIONS_CACHE_SERVICE_V2` | Empty |
<!-- markdownlint-enable MD013 -->

`ACTIONS_CACHE_SERVICE_V2` is cleared deliberately. sccache's GitHub Actions
backend selects the v2 cache service when that variable is set, and Ubicloud's
proxy serves v1, so leaving the runner's value in place sends sccache to an
endpoint the proxy does not implement.

## Failure modes

The action fails the step, with an `::error` annotation, when:

- `ACTIONS_CACHE_URL` is unset, which means the job is not on an Ubicloud
  runner;
- `ACTIONS_RUNTIME_TOKEN` is unset, so the proxy could not be authenticated;
- `ACTIONS_CACHE_URL` is not a valid URL;
- its host is not on a private network. A public host means this is a
  GitHub-hosted cache endpoint, and exporting it here would point sccache at
  the wrong service while claiming otherwise.

Private means an RFC 1918 IPv4 range, IPv4 loopback, `localhost`, or an IPv6
unique-local or loopback address.

The host must be a complete address literal, not merely start like one. A DNS
name such as `10.attacker.example` is refused: matching a private-range prefix
against the host would classify it as private and hand it the runtime token. A
name that happens to resolve to a private address is refused as well, because
resolution is not the action's to trust and Ubicloud's proxy URL carries a
literal. Legacy IPv4 forms are accepted where the URL parser has already
normalized them, so `http://2130706433/` is treated as the `127.0.0.1` it
denotes.

## Secrets

Both the runtime token and the proxy URL are registered as secrets before
anything is logged. The URL's path segment is bearer-like, so it is as
sensitive as the token itself. The action emits one `::notice` naming the
proxy's host and port and nothing else.

## Usage

Call it before any step that starts or configures sccache:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: ./.github/actions/export-ubicloud-cache-credentials
- name: Configure sccache
  shell: bash
  run: |
    echo "RUSTC_WRAPPER=sccache" >> "$GITHUB_ENV"
    echo "SCCACHE_GHA_ENABLED=true" >> "$GITHUB_ENV"
```

Order matters. `RUSTC_WRAPPER` and `SCCACHE_GHA_ENABLED` must be set after this
action and before any step that starts an sccache server or runs a build,
because sccache reads the cache configuration when its server starts and keeps
it for the life of that server.

## Release History

See [CHANGELOG](CHANGELOG.md).
