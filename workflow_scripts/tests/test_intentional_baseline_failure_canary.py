"""Temporary fixture proving mutation baseline failure diagnostics."""


def test_intentional_baseline_failure_canary() -> None:
    """Fail the clean baseline with a diagnostic suitable for artefact checks."""
    raise AssertionError("intentional baseline failure canary for final capture candidate")
