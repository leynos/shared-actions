# Changelog

## v1.0.0 (Unreleased)

- Move the package pin to `b83e3eb8`, which runs the Skylos scan under a fixed
  hash seed so the verdict no longer varies from run to run.

- Keep a `uv` that is already on `PATH`: run `setup-uv` only when `uv` is
  absent, so a calling workflow's pinned `uv` is not replaced for later steps.

- Add the df12-skylos action: provisions the pinned df12-python-lints commit
  through `uv tool run`, runs the installed command and reports its exit status.
