# Changelog

## v1.0.0 (Unreleased)

- Keep a `uv` that is already on `PATH`: run `setup-uv` only when `uv` is
  absent, so a calling workflow's pinned `uv` is not replaced for later steps.

- Add the df12-skylos action: provisions the pinned df12-python-lints commit
  through `uv tool run`, runs the installed command and reports its exit status.
