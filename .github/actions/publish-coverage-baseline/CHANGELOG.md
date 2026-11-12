# Changelog

## Unreleased

- A cache miss starts from the committed baseline file, fetched by a sparse
  checkout into the runner's temporary directory, instead of from 0.

- Initial release: ratchet and save the coverage figures a `generate-coverage`
  job handed over as an artefact, so a job on a runner provider whose cache
  store the pull-request lanes cannot read need not run a check that cannot
  fail.
