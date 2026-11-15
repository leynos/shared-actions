# Changelog

## Unreleased

- Add a `ratchet-floor` input (default 85), passed to the ratchet, so the
  published baseline follows the same floor rule as `generate-coverage`:
  coverage at or above the floor passes and the baseline tracks it, with a
  warning for a drop of more than one point. `ratchet-floor: 0` restores the
  previous behaviour exactly.

- A cache miss starts from the committed baseline file, fetched read from the
  contents API at the published commit, instead of from 0. A committed file
  that is not a percentage between 0 and 100 fails the job.

- Initial release: ratchet and save the coverage figures a `generate-coverage`
  job handed over as an artefact, so a job on a runner provider whose cache
  store the pull-request lanes cannot read need not run a check that cannot
  fail.
