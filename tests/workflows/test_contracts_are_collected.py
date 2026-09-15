"""Contract that every workflow contract in this directory is collected.

A contract module is only a gate if something runs it. `pytest.ini`
used to name the modules in this directory one by one, which reads as
deliberate and behaves as a trap: a module added here ran nowhere,
locally or in CI, and passed by never running. Two contracts added in
#480 were in that state until they were listed, and six modules already
here had never been listed at all. Twelve assertions among them run,
and were running nowhere.

So `testpaths` names the directory, and this module holds it to that.
The enumeration cannot come back, because an entry naming files rather
than the directory fails here even while every module that exists today
happens to be listed. That is the point: the defect is not a module
missing from a correct list, it is that a list exists to be forgotten.

The act-driven modules in this directory are not a reason to enumerate.
They gate themselves on a runtime probe and skip when it is absent, so
collecting them costs a skip rather than a failure.
"""

from __future__ import annotations

import configparser
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT: typ.Final[Path] = Path(__file__).resolve().parents[2]
PYTEST_CONFIGURATION: typ.Final[Path] = REPOSITORY_ROOT / "pytest.ini"

#: The directory this module is responsible for.
CONTRACT_DIRECTORY: typ.Final[Path] = Path(__file__).resolve().parent


def _configured_testpaths() -> list[Path]:
    """Return every `testpaths` entry, resolved against the repository."""
    parser = configparser.ConfigParser()
    parser.read(PYTEST_CONFIGURATION, encoding="utf-8")
    raw = parser.get("pytest", "testpaths", fallback="")
    return [(REPOSITORY_ROOT / entry).resolve() for entry in raw.split() if entry]


def _contract_modules() -> list[Path]:
    """Return every test module in this directory, sorted."""
    return sorted(CONTRACT_DIRECTORY.glob("test_*.py"))


def _covering_entry(module: Path, entries: cabc.Iterable[Path]) -> Path | None:
    """Return the `testpaths` entry that collects *module*, if any."""
    for entry in entries:
        if entry == module or entry in module.parents:
            return entry
    return None


def test_the_contract_directory_is_a_testpath() -> None:
    """`testpaths` names this directory, not the modules inside it.

    Asserting the directory rather than the coverage of today's modules
    is deliberate. A list that happens to name every module that exists
    passes a coverage check and still loses the next module added, which
    is the failure this rule exists to prevent.
    """
    entries = _configured_testpaths()
    assert CONTRACT_DIRECTORY in entries, (
        f"pytest.ini must list {CONTRACT_DIRECTORY.relative_to(REPOSITORY_ROOT)} "
        "as a testpaths entry, so that a module added there is collected "
        f"without anyone remembering to list it; entries are "
        f"{[str(entry.relative_to(REPOSITORY_ROOT)) for entry in entries]}"
    )


@pytest.mark.parametrize("module", _contract_modules(), ids=lambda path: path.name)
def test_every_contract_module_is_collected(module: Path) -> None:
    """Every module here is reached by some `testpaths` entry.

    The companion to the rule above, and the one that says what has
    gone wrong when it goes wrong: it names the module that would have
    run nowhere.
    """
    covering = _covering_entry(module, _configured_testpaths())
    assert covering is not None, (
        f"{module.relative_to(REPOSITORY_ROOT)} is collected by no testpaths "
        "entry, so it runs nowhere and passes by never running"
    )
