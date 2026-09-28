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


def _configured_testpaths(
    configuration: Path = PYTEST_CONFIGURATION,
) -> list[Path]:
    """Return every `testpaths` entry, resolved against the repository.

    `ConfigParser.read` skips a file it cannot open and says so only in
    its return value, so an unreadable `pytest.ini` would read as one
    with no entries. That, and a file without a `[pytest]` section, is
    refused here with the path named.

    Raises
    ------
    ValueError
        If *configuration* cannot be read or has no `[pytest]` section.
    """
    parser = configparser.ConfigParser()
    if not parser.read(configuration, encoding="utf-8"):
        msg = f"cannot read pytest configuration {configuration}"
        raise ValueError(msg)
    if not parser.has_section("pytest"):
        msg = f"{configuration} has no [pytest] section"
        raise ValueError(msg)
    raw = parser.get("pytest", "testpaths", fallback="")
    return [(REPOSITORY_ROOT / entry).resolve() for entry in raw.split() if entry]


def _contract_modules(directory: Path = CONTRACT_DIRECTORY) -> list[Path]:
    """Return every test module in *directory*, sorted.

    Listed with `iterdir` rather than `glob`, which returns nothing for a
    directory it cannot read, and an empty result is refused: the rule
    parametrised over it would otherwise pass by checking no module.

    Raises
    ------
    ValueError
        If *directory* cannot be listed or holds no test module.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as error:
        msg = f"cannot list contract modules in {directory}: {error}"
        raise ValueError(msg) from error
    modules = [
        path
        for path in entries
        if path.name.startswith("test_") and path.suffix == ".py"
    ]
    if not modules:
        msg = f"{directory} holds no test module to check"
        raise ValueError(msg)
    return modules


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


class TestTheReadBoundary:
    """What the two readers refuse, so the rules above cannot pass on nothing."""

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            pytest.param(None, "cannot read", id="missing-file"),
            pytest.param("[tool]\nx = 1\n", "no \\[pytest\\] section", id="no-section"),
        ],
    )
    def test_an_unusable_configuration_is_refused(
        self, tmp_path: Path, text: str | None, match: str
    ) -> None:
        """An unreadable or sectionless `pytest.ini` is not an empty one."""
        configuration = tmp_path / "pytest.ini"
        if text is not None:
            configuration.write_text(text, encoding="utf-8")

        with pytest.raises(ValueError, match=match):
            _configured_testpaths(configuration)

    def test_a_directory_with_no_module_is_refused(self, tmp_path: Path) -> None:
        """No modules to check is an error, not a pass over zero cases."""
        (tmp_path / "helper.py").write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="holds no test module"):
            _contract_modules(tmp_path)

    def test_a_directory_that_cannot_be_listed_is_refused(self, tmp_path: Path) -> None:
        """A missing directory raises rather than listing as empty."""
        with pytest.raises(ValueError, match="cannot list contract modules"):
            _contract_modules(tmp_path / "absent")
