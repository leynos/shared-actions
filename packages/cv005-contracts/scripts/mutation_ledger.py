"""Prove every contract clause by mutation, one clause at a time.

Each entry in `tests/mutations.toml` names a source file, one exact
substitution and the test that must fail once it is applied. The package is
copied to a temporary directory for each mutation, so the working tree is
never edited and nothing needs restoring. An anchor that does not occur
exactly once is itself a failure: a mutation that changes nothing, or
changes two places, proves nothing.

Only the tests whose name contains the entry's `expect` are run for a
mutation, since the verdict is whether that test fails; running the suite for
each of some two hundred mutations would cost far more than the answer needs.
Mutations run in parallel, each in its own copy.

Run it with `make mutation-ledger` from `packages/cv005-contracts`.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from plumbum import local

#: What a test name looks like, so `-k` can select it; an `expect` that is a
#: description of a parametrised case runs the whole suite instead.
IDENTIFIER = re.compile(r"\w+")
PACKAGE = Path(__file__).resolve().parents[1]
IGNORED = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc")


def _failing_tests(root: Path, expect: str) -> tuple[int, list[str]]:
    """Run the named test in a mutated copy and return its status and failures.

    A parametrised name is selected by the part before its brackets, and the
    verdict still requires the full name among the failures. An `expect` that
    is not a test name runs the whole suite.
    """
    pytest = local[sys.executable]["-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if IDENTIFIER.fullmatch(expect.split("[")[0]):
        pytest = pytest["-k", expect.split("[")[0]]
    # Bound to the command rather than set on `local`, whose working directory
    # and environment are process-wide and would race between the workers.
    bound = pytest.with_cwd(root).with_env(
        PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1"
    )
    status, stdout, _ = bound.run(retcode=None)
    failed = [line for line in stdout.splitlines() if line.startswith("FAILED")]
    return status, failed


def _run_one(entry: dict[str, str]) -> str:
    """Apply one mutation to a fresh copy and return its verdict line."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "pkg"
        shutil.copytree(PACKAGE, root, ignore=IGNORED)
        target = root / entry["file"]
        text = target.read_text(encoding="utf-8")
        count = text.count(entry["old"])
        if count != 1:
            return f"ANCHOR x{count:<3} {entry['id']}"
        target.write_text(text.replace(entry["old"], entry["new"], 1), encoding="utf-8")
        status, failed = _failing_tests(root, entry["expect"])
    if status != 0 and any(entry["expect"] in line for line in failed):
        return f"CAUGHT     {entry['id']}"
    verdict = "ELSEWHERE" if failed else "SURVIVED"
    return f"{verdict:10} {entry['id']}  {'; '.join(failed)[:300]}"


def main() -> int:
    """Run every mutation in the ledger; exit 1 unless all are caught."""
    ledger = tomllib.loads((PACKAGE / "tests" / "mutations.toml").read_text())
    entries = ledger["mutation"]
    only = set(sys.argv[1:])
    chosen = [entry for entry in entries if not only or entry["id"] in only]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
        lines = list(pool.map(_run_one, chosen))
    for line in lines:
        print(line)
    caught = sum(line.startswith("CAUGHT") for line in lines)
    print(f"{caught}/{len(lines)} caught")
    return 0 if caught == len(lines) else 1


if __name__ == "__main__":
    sys.exit(main())
