# Changelog

## Unreleased

- Initial version. Republishes Ubicloud's cache proxy credentials, which the
  runner exposes to action steps only, through `GITHUB_ENV` so later shell
  steps can reach the proxy. Clears `ACTIONS_CACHE_SERVICE_V2` so sccache uses
  the v1 endpoint the proxy serves, masks the runtime token and the proxy URL,
  and fails closed when the cache URL is absent or not on a private network.
  A host counts as private only when it is a complete address literal inside a
  private range, so a DNS name beginning with a private octet cannot pass.
