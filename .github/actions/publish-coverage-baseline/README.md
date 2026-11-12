# publish-coverage-baseline

Ratchets the coverage figures a `generate-coverage` job handed over, and saves
the advanced baseline to the cache store of the runner it runs on. It is the
second of three jobs when main coverage is split across runner providers; see
"Splitting main coverage across runner providers" in the `generate-coverage`
README.

It compiles nothing and measures nothing. It downloads the hand-over artefact,
restores the current baseline with the same key prefix and path list the
pull-request lanes restore, compares with `ratchet_coverage.py` from
`generate-coverage`, and saves only if the comparison passed. The comparison is
the ratchet's: a figure more than its one-point tolerance below the baseline
fails the job and saves nothing; one inside the band passes and holds the
baseline; one above the band advances it. The save runs only on a push to
`refs/heads/main`.

A missing baseline, such as a first run or an evicted cache, starts at 0, as in
`generate-coverage`: the first figure becomes the baseline. The job says so in
a notice. It also prints the `metric ratchet-cache.restore` and
`metric ratchet-cache.save` lines `generate-coverage` prints.

## Serialization

The job restores, compares and saves in one run, so two overlapping runs could
each restore the same baseline and save in the opposite order. Give the
workflow a concurrency group that never cancels and never overlaps, as the
example does, so publications happen in push order.

## Inputs

| Name                   | Description                                              | Required | Default                     |
| ---------------------- | -------------------------------------------------------- | -------- | --------------------------- |
| `artefact-name`        | The `baseline-artefact-name` the measuring job was given | yes      |                             |
| `baseline-rust-file`   | Rust baseline path; match `generate-coverage`            | no       | `.coverage-baseline.rust`   |
| `baseline-python-file` | Python baseline path; match `generate-coverage`          | no       | `.coverage-baseline.python` |

## Example

```yaml
concurrency:
  group: coverage-main-${{ github.ref }}
  cancel-in-progress: false

jobs:
  baseline:
    needs: coverage
    runs-on: ubicloud-standard-2
    steps:
      - uses: leynos/shared-actions/.github/actions/publish-coverage-baseline@<sha>
        with:
          artefact-name: coverage-baseline
```

The job needs no checkout: the action restores the baseline files into the
workspace and saves them from there. On a cache miss it fetches the committed
baseline files (and only those, into a directory of its own, so a caller's
workspace is untouched) and starts from them; with no committed file it starts
at 0. The job needs `contents: read`. It needs no `if` either, and a publisher
job under the CV-005 rules may not carry one: the action's first step checks
for a push to the trunk and every later step runs only then, so a dispatch
leaves the job green with nothing published.
