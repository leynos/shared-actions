# Composite Actions vs Full Workflows — Key Limitations

Composite actions are brilliant for bundling a handful of steps you want to
reuse, but they remain a *single* workflow-step in the caller job. That design
imposes several hard limits you don’t face when you write a full workflow.

| Area | Composite action | Full workflow |
| --- | --- | --- |
| **Jobs** | No custom jobs, runners, matrix | Full job orchestration |
| **Triggers** | No `on:` events or schedules | Direct event response |
| **Config keys** | Only `using`, `steps`, `pre`, `post` | Full YAML surface |
| **Secrets** | No auto access, must pass via inputs | Direct read from config |
| **Logging** | Single line, steps hidden | Per-job/step logging |
| **Conditionals** | Step-level only | Job/step/workflow level |
| **Outputs** | Strings only, no artifacts | Artifacts, cache, complex |
| **Nesting** | No self-references | 20 workflows, 4 levels |

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
