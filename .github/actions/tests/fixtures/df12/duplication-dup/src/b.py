"""Module b."""


def describe(values: list[int]) -> dict[str, float | int | None]:
    """Return a report for the values."""
    total = 0
    count = 0
    largest = None
    for value in values:
        total += value
        count += 1
        if largest is None or value > largest:
            largest = value
    mean = total / count if count else 0
    return {"total": total, "count": count, "largest": largest, "mean": mean}
