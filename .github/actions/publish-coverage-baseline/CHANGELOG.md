# Changelog

## Unreleased

- Initial release: ratchet and save the coverage figures a `generate-coverage`
  job handed over as an artefact, so a job on a runner provider whose cache
  store the pull-request lanes cannot read need not run a check that cannot
  fail.
