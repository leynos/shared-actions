"""Fixture module."""


def _private_unused() -> int:
    """Nothing calls this."""
    return 2


def main() -> int:
    """Return a value."""
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
