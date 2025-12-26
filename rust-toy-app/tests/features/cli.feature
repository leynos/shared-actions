# CLI Feature - Test Fixture for generate-coverage Action
#
# This feature file is part of the test fixture suite for validating the
# `generate-coverage` GitHub Action's cucumber-rs integration.
# It is NOT a functional product specification.
#
# The scenarios exercise CLI behavior to demonstrate integration testing
# under coverage collection.

Feature: Command-line interface
  As a user of rust-toy-app
  I want to invoke the CLI with different arguments
  So that I can get greetings from the command line

  Scenario: CLI shows help with --help flag
    Given the rust-toy-app binary
    When I run it with "--help"
    Then the exit code should be 0
    And the output should contain "Usage"

  Scenario: CLI shows help with -h flag
    Given the rust-toy-app binary
    When I run it with "-h"
    Then the exit code should be 0
    And the output should contain "Usage"

  Scenario: CLI greets world by default
    Given the rust-toy-app binary
    When I run it without arguments
    Then the exit code should be 0
    And the output should be "Hello, world!"

  Scenario: CLI greets with provided name using long flag
    Given the rust-toy-app binary
    When I run it with "--name Alice"
    Then the exit code should be 0
    And the output should be "Hello, Alice!"

  Scenario: CLI greets with provided name using short flag
    Given the rust-toy-app binary
    When I run it with "-n Bob"
    Then the exit code should be 0
    And the output should be "Hello, Bob!"

  Scenario: CLI fails on unknown flag
    Given the rust-toy-app binary
    When I run it with "--unknown"
    Then the exit code should be 2
    And the stderr should contain "error:"
