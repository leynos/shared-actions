# Composite Actions vs Full Workflows — Key Limitations

Composite actions are brilliant for bundling a handful of steps you want to
reuse, but they remain a *single* workflow-step in the caller job. That design
imposes several hard limits you don’t face when you write a full workflow.

| Area                      | What a **composite action can’t** do                                                                                                                                                                                                                                                          | Why a **full workflow** can                                                                                             |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Jobs & runners**        | Define its own `jobs`, `runs-on`, `services`, `container`, `strategy.matrix`, `timeout-minutes`, `concurrency`, `needs`, or `permissions`.                                                                                                                                                    | A workflow owns the job graph, so it can orchestrate multiple jobs on different runners.                                |
| **Triggers**              | Cannot specify `on:` events, `workflow_dispatch`, `schedule`, etc.                                                                                                                                                                                                                            | Workflows are entry-points; they respond to events directly.                                                            |
| **Top-level keys**        | Keys such as `env`, `defaults`, `shell`, `continue-on-error`, `timeout-minutes`, `permissions` are **not permitted** under `runs:`. Only `using`, `steps` (plus `pre`, `post`, `post-if`) are allowed. ([docs.github.com](http://docs.github.com), [docs.github.com](http://docs.github.com)) | Workflows accept the full YAML surface.                                                                                 |
| **Secrets & variables**   | Has *no* automatic access to repository/organisation secrets, variables, or `vault`. The caller must pass data explicitly via `inputs` or per-step `env`.                                                                                                                                     | Workflows can read any secret/variable configured for the run.                                                          |
| **Logging & visibility**  | The runner log collapses everything to one line (`uses: author/repo@ref`). Inner steps appear only if you expand the step, making per-step timing and annotations harder to read. ([docs.github.com](http://docs.github.com))                                                                 | Each job and step is logged separately by default.                                                                      |
| **Conditionals & matrix** | No top-level `if:` or matrix-level filtering. Conditionals have to live on individual steps inside the composite.                                                                                                                                                                             | Workflows support `if:` at job, step, and reusable-workflow call sites, plus full matrix fan-out.                       |
| **Outputs**               | Can emit outputs, but only simple strings set by `echo "::set-output name=foo::bar"`; cannot expose artefacts or cache scopes of its own.                                                                                                                                                     | Workflows can upload/download artefacts, define cache keys, and expose complex outputs between jobs.                    |
| **Nested depth**          | Useful inside a single job, but cannot *itself* call another composite action that lives in the *same* repository path (circular reference guard).                                                                                                                                            | Workflows may call up to 20 distinct reusable workflows (four levels deep). ([docs.github.com](http://docs.github.com)) |

______________________________________________________________________

## Practical consequences

- **Environment propagation** – If your composite needs tokens,
  checksums, feature flags, etc., the caller must map them in:

  ```yaml
  - uses: org/upload-codescene-coverage@v1
    with:
      access-token: ${{ secrets.CS_TOKEN }}
      installer-checksum: ${{ vars.CLI_SHA256 }}
  
  ```

- **No parallelism inside** – You cannot spin up *n* test jobs or matrix
  variants from inside the composite; do that in the workflow.

- **Debugging friction** – Because logs collapse, iterative debugging is
  slower; wrap flaky commands in verbose `run:` blocks or add `--debug` flags
  inside the composite.

- **Upgrade path** – When a composite starts needing multiple jobs,
  secrets, or different runners, promote it to a *reusable workflow* instead;
  that removes nearly all the above constraints while still avoiding
  copy-and-paste.

______________________________________________________________________

## Rule of thumb

*Use a composite action* for a handful of **steps** that always run together on
**one runner** and need only the data you feed them.\
*Reach for a reusable workflow* (or a plain workflow) as soon as you need
**jobs**, **triggers**, **matrix**, or richer environment features.
