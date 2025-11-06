# Generate Coverage — Design Notes

This document captures the architectural choices for the `generate-coverage`
action and the evolution of its supporting scripts.

## Design Decisions

- *2025-11-06* — Coverage artefact names now include the runner operating
  system and architecture, with an optional caller-provided suffix. The
  metadata is computed by `set_outputs.py`, which detects the platform via a
  `plumbum`-driven Python subprocess and exposes the composed name to the
  workflow. The script migrated to `cyclopts` for CLI parsing so additional
  inputs can be mapped declaratively from the GitHub Actions environment.

## Roadmap

- [x] Extend artefact naming to include platform metadata and support custom
  suffixes.
