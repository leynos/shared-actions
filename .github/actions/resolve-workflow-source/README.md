# resolve-workflow-source

Resolves the repository and commit SHA of the reusable workflow that is
currently executing, via the GitHub OIDC token's `job_workflow_ref` and
`job_workflow_sha` claims. Reusable workflows use it to check out their
own helper scripts at exactly the version the caller pinned, keeping
workflow and scripts in lockstep.

Under [act](https://github.com/nektos/act) (detected via `ACT=true`),
the action short-circuits: the workspace already contains the workflow
source, so no checkout is needed.

## Outputs

| Output | Meaning |
| ------ | ------- |
| `checkout` | `"true"` when the workflow repository must be checked out; `"false"` under act. |
| `workflow_dir` | Directory containing (or destined for) the workflow source. |
| `repo` | Workflow repository in `owner/name` form. |
| `ref` | Workflow commit SHA (empty under act). |

## Usage

```yaml
- name: Resolve workflow source
  id: workflow-source
  uses: leynos/shared-actions/.github/actions/resolve-workflow-source@<sha>

- name: Checkout workflow repository
  if: ${{ steps.workflow-source.outputs.checkout == 'true' }}
  uses: actions/checkout@<sha>
  with:
    repository: ${{ steps.workflow-source.outputs.repo }}
    ref: ${{ steps.workflow-source.outputs.ref }}
    path: workflow-src
    fetch-depth: 1
    persist-credentials: false
```

The calling job needs `id-token: write` permission for the OIDC token
request; the action fails fast with a clear message when it is missing.

## Pinning caveat

Reference this action with a **fully pinned commit SHA** and bump the
pin manually when the action changes. The action performs the workflow
SHA resolution itself, so its own reference is the one thing that
cannot be resolved dynamically — a relative `./` path would resolve
against the *caller's* repository inside a reusable workflow.

## Testing

`tests/workflows/test_resolve_workflow_source.py` exercises the act
short-circuit and the OIDC fail-fast branches through the
`test-resolve-workflow-source.yml` wrapper under act. The OIDC happy
path cannot run outside real GitHub infrastructure; it is validated by
every real run of the dependabot-automerge and mutation-testing
reusable workflows.
