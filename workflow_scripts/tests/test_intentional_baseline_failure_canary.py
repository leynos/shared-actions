def test_intentional_baseline_failure_canary():
    """Fail the clean baseline to verify retained diagnostics."""
    print("INTENTIONAL_BASELINE_FAILURE_CANARY")
    raise AssertionError(
        "intentional baseline failure canary for diagnostic retention"
    )
