# Greeting Feature - Test Fixture for generate-coverage Action
#
# This feature file is part of the test fixture suite for validating the
# `generate-coverage` GitHub Action's cucumber-rs integration.
# It is NOT a functional product specification.
#
# The scenarios exercise the greeting functionality to generate meaningful
# code coverage metrics.

Feature: Greeting generation
  As a user of rust-toy-app
  I want to receive a personalized greeting
  So that I feel welcomed by the application

  Scenario: Default greeting without a name
    Given no name is provided
    When the greeting is generated
    Then the output should be "Hello, world!"

  Scenario: Personalized greeting with a name
    Given a name "Alice" is provided
    When the greeting is generated
    Then the output should be "Hello, Alice!"

  Scenario: Greeting with a different name
    Given a name "Bob" is provided
    When the greeting is generated
    Then the output should be "Hello, Bob!"

  Scenario Outline: Various names produce correct greetings
    Given a name "<name>" is provided
    When the greeting is generated
    Then the output should be "Hello, <name>!"

    Examples:
      | name     |
      | Charlie  |
      | Diana    |
      | Eve      |

  # Edge Case Scenarios
  # These scenarios validate the same edge cases as src/lib.rs and tests/bdd.rs
  # to ensure cross-harness coverage validation.

  Scenario: Empty name produces minimal greeting
    Given a name "" is provided
    When the greeting is generated
    Then the output should be "Hello, !"

  Scenario: Whitespace-only name is preserved
    Given a name "   " is provided
    When the greeting is generated
    Then the output should be "Hello,    !"

  Scenario: Very long name is handled correctly
    Given a name "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" is provided
    When the greeting is generated
    Then the output should be "Hello, AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA!"
