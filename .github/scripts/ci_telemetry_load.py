#!/usr/bin/env python3
"""Hold *k* processors at 100% for a fixed time, for the telemetry calibration.

Usage: ci_telemetry_load.py {one|all} SECONDS

``one`` spins one process and ``all`` spins one per processor the job may use.
Processes, not threads, so the interpreter lock cannot cap the load at one core.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time


def spin(seconds: float) -> None:
    """Busy-loop for *seconds* of wall time."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


def main(argv: list[str]) -> int:
    """Run the load; return 2 on a usage error."""
    if len(argv) != 3 or argv[1] not in {"one", "all"}:
        print(f"usage: {argv[0]} {{one|all}} SECONDS", file=sys.stderr)
        return 2
    count = 1 if argv[1] == "one" else len(os.sched_getaffinity(0))
    seconds = float(argv[2])
    print(f"ci-telemetry calibration: {count} busy process(es) for {seconds:g}s")
    workers = [
        multiprocessing.Process(target=spin, args=(seconds,)) for _ in range(count)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
