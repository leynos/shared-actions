Feature: Default UUIDv7 correlation IDs

  Scenario: Generate a default UUIDv7 correlation ID
    When I generate a default UUIDv7 correlation ID
    Then the ID is a lowercase hex string of length 32
    And the ID has RFC 4122 version and variant bits
    And the timestamp is within the request window

  Scenario: Generated UUIDv7 values are unique
    When I generate 128 correlation IDs
    Then all generated IDs are unique
