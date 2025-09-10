#!/usr/bin/env bats

@test "runs the placeholder script" {
  run "$BATS_TEST_DIRNAME/../src/main.sh"
  [ "$status" -eq 0 ]
}
