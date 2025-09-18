# Scripting standards

Wildside scripts favour clarity, reproducibility, and testability. The
baseline tooling is Python and the [`uv`](https://github.com/astral-sh/uv)
launcher so that scripts remain dependency-self-contained and easy to execute
in Continuous Integration (CI) or locally.

## Language and runtime

- Target Python 3.13 for all new scripts. Older versions may only be used when
  integration constraints require them, and any exception must be documented
  inline.
- Each script starts with an `uv` script block so runtime and dependency
  expectations travel with the file. Prefer the shebang `#!/usr/bin/env -S uv
  run python` followed by the metadata block shown in the example below.
- External processes are invoked via [`plumbum`](https://plumbum.readthedocs.io)
  to provide structured command execution rather than ad-hoc shell strings.
- File-system interactions use `pathlib.Path`. Higher-level operations (for
  example, copying or removing trees) go through the `shutil` standard library
  module.

```python
#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["plumbum", "cmd-mox"]
# ///

from __future__ import annotations

from pathlib import Path
from plumbum import local
from plumbum.cmd import tofu


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    cluster_dir = project_root / "infra" / "clusters" / "dev"
    with local.cwd(cluster_dir):
        tofu["plan"]()


if __name__ == "__main__":
    main()
```

## Testing expectations

- Every script requires automated coverage via `pytest`. Use `pytest-mock` for
  fixture-driven mocking of Python objects and
  [`cmd-mox`](https://github.com/leynos/cmd-mox/) to simulate external
  executables without touching the host system.
- Behavioural flows that map cleanly to scenarios should adopt
  Behaviour-Driven Development (BDD) via `pytest-bdd` so
  the intent of the script is captured in human-readable Given/When/Then
  narratives.
- Tests reside in `scripts/tests/` mirroring the script names. For example,
  `scripts/bootstrap_doks.py` pairs with
  `scripts/tests/test_bootstrap_doks.py`.
- When scripts rely on environment variables, assert both the happy path and
  failure modes; the tests should demonstrate graceful error handling rather
  than raising opaque stack traces.

## Operational guidelines

- Scripts must be idempotent. Re-running them should converge state without
  destructive side effects. Use guard conditions (for example, checking the
  HashiCorp Vault secrets manager for existing secrets) before writing or
  rotating credentials.
- Prefer pure functions that accept configuration objects over global state so
  tests can exercise the logic deterministically.
- Exit codes should follow UNIX conventions: `0` for success, non-zero for
  actionable failures. Surface human-friendly error messages that highlight the
  remediation steps.
- Keep dependencies minimal. If a new package is required, add it to the `uv`
  block and document the rationale inside the script or companion tests.

This document should be referenced when introducing or updating automation
scripts to maintain a consistent developer experience across the repository.
