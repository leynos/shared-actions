#!/usr/bin/env python3
"""Judge one calibration run against G3's criteria and print the evidence.

Usage: ci_telemetry_verdict.py RECORD_JSON LABEL LABEL_CORES LOAD

The criteria, from the CI observability plan's H3 and gate G3: `nproc` equals
the label's vCPU count; measured busy cores read k +/- 0.2 and within 10% of k
(k = 1 for load `one`, `nproc` for `all`); steal stays under 5% of CPU time.
The script reports and never fails, so a falsified run is still evidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOLERANCE_CORES = 0.2
TOLERANCE_RATIO = 0.10
STEAL_LIMIT = 0.05
#: Records round to four places, so a reading exactly on a tolerance can differ
#: from it by float noise; the tolerances are inclusive.
EPSILON = 1e-9


def judge(record: dict[str, object], label_cores: int, load: str) -> dict[str, object]:
    """Return the run's measurements and per-criterion verdicts."""
    nproc = record["nproc"]
    k = 1 if load == "one" else nproc
    avg = record["cpu_busy_cores_avg"]
    steal = record["cpu_steal_fraction"]
    measured = isinstance(avg, (int, float)) and isinstance(nproc, int)
    return {
        "nproc": nproc,
        "label_cores": label_cores,
        "k": k,
        "busy_avg": avg,
        "busy_p95": record["cpu_busy_cores_p95"],
        "busy_max": record["cpu_busy_cores_max"],
        "steal": steal,
        "samples": record["sample_count"],
        "nproc_matches_label": nproc == label_cores,
        "busy_within_0_2": measured and abs(avg - k) <= TOLERANCE_CORES + EPSILON,
        "busy_within_10pct": measured and abs(avg - k) <= TOLERANCE_RATIO * k + EPSILON,
        "steal_under_5pct": isinstance(steal, (int, float)) and steal < STEAL_LIMIT,
    }


def main(argv: list[str]) -> int:
    """Print the verdict as a notice annotation and a summary row."""
    if len(argv) != 5:
        print(f"usage: {argv[0]} RECORD_JSON LABEL LABEL_CORES LOAD", file=sys.stderr)
        return 2
    record = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    verdict = {
        "label": argv[2],
        "load": argv[4],
        **judge(record, int(argv[3]), argv[4]),
    }
    body = json.dumps(verdict, sort_keys=True)
    print(f"::notice title=ci-telemetry calibration::{body}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
