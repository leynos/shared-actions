# publish-coverage-baseline

Ratchets the coverage figures a `generate-coverage` job handed over, and saves
the advanced baseline to the cache store of the runner it runs on. It is the
second of three jobs when main coverage is split across runner providers; see
"Splitting main coverage across runner providers" in the `generate-coverage`
README.

It compiles nothing and measures nothing. It downloads the hand-over artefact,
restores the current baseline with the same key prefix and path list the
pull-request lanes restore, compares with `ratchet_coverage.py` from
`generate-coverage`, and saves only if the comparison passed. A lower figure
fails the job and saves nothing. The save runs only on a push to
`refs/heads/main`.

## Inputs

| Name                   | Description                                              | Required | Default                     |
| ---------------------- | -------------------------------------------------------- | -------- | --------------------------- |
| `artefact-name`        | The `baseline-artefact-name` the measuring job was given | yes      |                             |
| `baseline-rust-file`   | Rust baseline path; match `generate-coverage`            | no       | `.coverage-baseline.rust`   |
| `baseline-python-file` | Python baseline path; match `generate-coverage`          | no       | `.coverage-baseline.python` |

## Example

```yaml
baseline:
  needs: coverage
  if: github.event_name == 'push'
  runs-on: ubicloud-standard-2
  steps:
    - uses: leynos/shared-actions/.github/actions/publish-coverage-baseline@<sha>
      with:
        artefact-name: coverage-baseline
```

The job needs no checkout: the action restores the baseline files into the
workspace and saves them from there.
