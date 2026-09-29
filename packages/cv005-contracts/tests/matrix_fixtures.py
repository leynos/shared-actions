"""A matrix-job tree, and one publisher that measures by `make coverage`.

Two shapes the estate rule cannot pair by inspection. `MATRIX_TREE` is a
pull-request job whose three coverage legs are chosen by conditions and read
their inputs from a matrix, against a publisher whose Windows baseline is
written by a job of its own. `MAKE_TREE` measures by running a repository
recipe, so it has no `generate-coverage` step for the estate rule to find.
Each is compliant once its declarations are written, as `MATRIX_CONFIG` and
`MAKE_CONFIG` write them.
"""

from __future__ import annotations

import textwrap
import typing as typ

from contract_fixtures import PIN, SHARED

if typ.TYPE_CHECKING:
    from pathlib import Path

LINUX_LEG: typ.Final[str] = "ci.yml:build-test:Coverage (Linux)"
WINDOWS_LEG: typ.Final[str] = "ci.yml:build-test:Coverage (Windows)"
STRICT_LEG: typ.Final[str] = "ci.yml:build-test:Coverage (Windows, strict)"
UPLOAD_LEG: typ.Final[str] = "coverage-main.yml:coverage-upload:Generate coverage"
BASELINE_LEG: typ.Final[str] = (
    "coverage-main.yml:coverage-baseline-windows:Generate coverage"
)

MATRIX_LANE: typ.Final[str] = textwrap.dedent(f"""\
    name: CI
    on:
      push:
        branches: [main]
      pull_request:
    jobs:
      build-test:
        runs-on: ${{{{ matrix.os }}}}
        permissions:
          contents: read
        strategy:
          matrix:
            include:
              - os: ubuntu-latest
                features: ''
                with-default-features: true
              - os: windows-latest
                features: ''
                with-default-features: true
              - os: windows-latest
                features: strict
                with-default-features: false
        steps:
          - uses: actions/checkout@v4
          - name: Coverage (Linux)
            if: ${{{{ runner.os == 'Linux' && github.event_name == 'pull_request' }}}}
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              all-features: 'true'
              with-ratchet: 'true'
              publish-artefact: 'false'
          - name: Coverage (Windows)
            if: >-
              ${{{{ runner.os == 'Windows' && matrix.features == ''
              && github.event_name == 'pull_request' }}}}
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              features: base
              with-default-features: ${{{{ matrix.with-default-features }}}}
              with-ratchet: 'true'
              publish-artefact: 'false'
          - name: Coverage (Windows, strict)
            if: >-
              ${{{{ runner.os == 'Windows' && matrix.features != ''
              && github.event_name == 'pull_request' }}}}
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              features: base ${{{{ matrix.features }}}}
              with-default-features: ${{{{ matrix.with-default-features }}}}
              with-ratchet: 'true'
              publish-artefact: 'false'
    """)

MATRIX_PUBLISHER: typ.Final[str] = textwrap.dedent(f"""\
    name: Coverage (main)
    on:
      push:
        branches: [main]
    permissions: {{}}
    concurrency:
      group: coverage-main-${{{{ github.ref }}}}
      cancel-in-progress: false
    jobs:
      coverage-upload:
        runs-on: ubuntu-latest
        environment: codescene
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
            with:
              persist-credentials: false
          - name: Generate coverage
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              all-features: 'true'
              with-ratchet: 'true'
          - name: Check for the CodeScene token
            id: codescene-token
            run: echo "available=${{{{ secrets.CS_ACCESS_TOKEN != '' }}}}" >> "$GITHUB_OUTPUT"
          - name: Upload coverage data to CodeScene
            if: steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main'
            uses: {SHARED}/upload-codescene-coverage@{PIN}
            with:
              path: coverage.xml
              access-token: ${{{{ secrets.CS_ACCESS_TOKEN }}}}
      coverage-baseline-windows:
        runs-on: windows-latest
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
            with:
              persist-credentials: false
          - name: Generate coverage
            uses: {SHARED}/generate-coverage@{PIN}
            with:
              output-path: coverage.xml
              features: base
              with-default-features: true
              with-ratchet: 'true'
    """)  # noqa: E501 - two lines must match the real workflow's.

MATRIX_TREE: typ.Final[dict[str, str]] = {
    "ci.yml": MATRIX_LANE,
    "coverage-main.yml": MATRIX_PUBLISHER,
}

MATRIX_CONFIG: typ.Final[str] = f"""\
repository = "leynos/example"

[[pairing]]
lane = "{LINUX_LEG}"
publisher = "{UPLOAD_LEG}"
guards = ["runner.os == 'Linux'"]

[[pairing]]
lane = "{WINDOWS_LEG}"
publisher = "{BASELINE_LEG}"
guards = ["runner.os == 'Windows'", "matrix.features == ''"]
[pairing.matrix]
with-default-features = "true"

[[pairing]]
lane = "{STRICT_LEG}"
publisher = "{BASELINE_LEG}"
differs = ["features", "with-default-features"]
guards = ["runner.os == 'Windows'", "matrix.features != ''"]
[pairing.matrix]
features = "strict"
with-default-features = "false"
"""

MAKE_LANE: typ.Final[str] = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [main]
      pull_request:
    jobs:
      coverage-check:
        runs-on: ubuntu-latest
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
          - name: Measure coverage
            run: make coverage
    """)

MAKE_PUBLISHER: typ.Final[str] = textwrap.dedent(f"""\
    name: Coverage (main)
    on:
      push:
        branches: [main]
    permissions: {{}}
    concurrency:
      group: coverage-main-${{{{ github.ref }}}}
      cancel-in-progress: false
    jobs:
      coverage-upload:
        runs-on: ubuntu-latest
        environment: codescene
        permissions:
          contents: read
        steps:
          - uses: actions/checkout@v4
            with:
              persist-credentials: false
          - name: Generate coverage
            run: make coverage
          - name: Check for the CodeScene token
            id: codescene-token
            run: echo "available=${{{{ secrets.CS_ACCESS_TOKEN != '' }}}}" >> "$GITHUB_OUTPUT"
          - name: Upload coverage data to CodeScene
            if: steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main'
            uses: {SHARED}/upload-codescene-coverage@{PIN}
            with:
              path: lcov.info
              format: lcov
              access-token: ${{{{ secrets.CS_ACCESS_TOKEN }}}}
    """)  # noqa: E501 - two lines must match the real workflow's.

MAKE_TREE: typ.Final[dict[str, str]] = {
    "ci.yml": MAKE_LANE,
    "coverage-main.yml": MAKE_PUBLISHER,
}

#: The clauses that find nothing to measure, for a repository that runs a
#: recipe instead of `generate-coverage`, and the exceptions that waive them.
WAIVED_CLAUSES: typ.Final[tuple[str, ...]] = (
    "publisher.wiring",
    "coverage.pull-request-lane",
    "coverage.selection-parity",
)
MAKE_CONFIG: typ.Final[str] = 'repository = "leynos/example"\n' + "".join(
    f"""
[[exception]]
clause = "{clause}"
ruling = "leynos/example#444"
reason = "Coverage comes from `make coverage`, which the shared action cannot run."
requires = "make coverage"
"""
    for clause in WAIVED_CLAUSES
)


def write_repository(root: Path, texts: dict[str, str], config: str) -> Path:
    """Write workflow texts and a configuration under a repository root.

    Returns
    -------
    Path
        The repository root, for the command or the API to read.

    """
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name, text in texts.items():
        (workflows / name).write_text(text, encoding="utf-8")
    (root / ".github" / "cv005.toml").write_text(config, encoding="utf-8")
    return root
