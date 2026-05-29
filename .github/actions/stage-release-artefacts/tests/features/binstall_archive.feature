Feature: cargo-binstall archive staging
  Scenario: staging a Linux target creates a cargo-binstall archive
    Given a workspace with a Cargo package named "myapp" at version "1.2.3"
    And a release binary for target "x86_64-unknown-linux-gnu"
    And stage-release-artefacts has cargo-binstall archive creation enabled
    When the staging action runs for target "linux-x86_64"
    Then the staged files include "myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz"
    And the archive contains "myapp" at the root
    And a SHA-256 sidecar exists for the archive
    And the GitHub output includes "binstall_archive_path"
