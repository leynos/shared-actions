"""Checkout builders and run outcomes shared by the mutation preflight tests.

One builder writes a mutation-ready checkout, and one dataclass groups the
knobs a test turns. Keeping them here means each phase test reads as a
statement about a declaration rather than about how a checkout is assembled.
"""

from __future__ import annotations

import dataclasses as dc
import sys
import typing as typ
from pathlib import Path

import pytest

# The fake tool shims are POSIX shell scripts; the reusable workflows only run
# on ubuntu-latest, and on Windows the real tools would answer instead.
POSIX_SHIMS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="fake tool helpers emit POSIX sh"
)

# Creating a symlink needs an elevated token on Windows, and the reusable
# workflows only run on ubuntu-latest, where the containment check matters.
POSIX_SYMLINKS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="creating symlinks needs privileges on Windows"
)

# mutmut's sandbox copies ``workflow_scripts/`` but not ``packages/``, so the
# shared requires-python rule (#565) is absent when these tests run under it.
# No range can be judged without the rule, so the honest outcome is a skip.
RULE_AVAILABLE = pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "cv005-contracts"
        / "cv005_contracts"
        / "requires_python.py"
    ).is_file(),
    reason="mutmut's sandbox does not copy the shared requires-python rule",
)

MUTMUT_TABLE = """\
[tool.mutmut]
source_paths = ["workflow_scripts/"]
pytest_add_cli_args_test_selection = ["workflow_scripts/tests/"]
also_copy = [{also_copy}]
"""


@dc.dataclass(frozen=True, slots=True)
class Checkout:
    """The knobs one test turns when it writes a mutation-ready checkout.

    Attributes
    ----------
    requires_python : str | None
        The project's declared range, or ``None`` to declare none. Declaring
        none keeps the interpreter phase from loading the shared rule, which
        mutmut's sandbox does not copy.
    also_copy : tuple[str, ...]
        What ``[tool.mutmut] also_copy`` names.
    manifest : str | None
        The sidecar declaration, when the test wants one.
    extra : dict[str, str]
        Further files to write, keyed by path relative to the checkout.

    """

    requires_python: str | None = None
    also_copy: tuple[str, ...] = ("conftest.py",)
    manifest: str | None = None
    extra: dict[str, str] = dc.field(default_factory=dict)


def write_file(root: Path, relative: str, text: str = "") -> None:
    """Write *text* at *relative* under *root*, creating parent directories.

    Parameters
    ----------
    root : Path
        The checkout the file belongs to.
    relative : str
        The file's path, relative to *root*.
    text : str
        What to write; an empty file by default.

    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repository(root: Path, checkout: Checkout | None = None) -> Path:
    """Write a mutation-ready checkout and return its root.

    Parameters
    ----------
    root : Path
        Where the checkout is written.
    checkout : Checkout | None
        What to declare and write; the defaults when ``None``.

    Returns
    -------
    Path
        *root*, after the fixture files are written.

    """
    knobs = checkout or Checkout()
    project = '[project]\nname = "example"\n'
    if knobs.requires_python is not None:
        project += f'requires-python = "{knobs.requires_python}"\n'
    quoted = ", ".join(f'"{entry}"' for entry in knobs.also_copy)
    configured = project + "\n" + MUTMUT_TABLE.format(also_copy=quoted)
    write_file(root, "pyproject.toml", configured)
    write_file(root, "conftest.py", "")
    write_file(root, "workflow_scripts/example.py", "VALUE = 1\n")
    write_file(root, "workflow_scripts/tests/test_example.py", "")
    for relative, extra_text in knobs.extra.items():
        write_file(root, relative, extra_text)
    if knobs.manifest is not None:
        write_file(root, ".github/mutation-preflight.toml", knobs.manifest)
    return root


def lock_document(version: str, source: str) -> str:
    """Return a ``uv.lock`` pinning one package at *version* from *source*.

    Parameters
    ----------
    version : str
        The version the lock records for ``tei-rapporteur``.
    source : str
        The body of the lock entry's ``source`` table.

    Returns
    -------
    str
        A lock document a declaration can be checked against.

    """
    return (
        "version = 1\n\n"
        "[[package]]\n"
        'name = "example"\nversion = "0.1.0"\n\n'
        "[[package]]\n"
        'name = "tei-rapporteur"\n'
        f'version = "{version}"\n'
        f"source = {{ {source} }}\n"
    )


def locked_checkout(manifest: str, source: str = 'registry = "x"') -> Checkout:
    """Return a checkout declaring *manifest*, with its native package locked."""
    return Checkout(
        manifest=manifest,
        extra={"uv.lock": lock_document("0.3.1", source)},
    )


class MissingResultError(KeyError):
    """Raised when a phase reported no result under the name a test asked for."""

    def __init__(self, phase: str, name: str) -> None:
        """Name the phase and the result a test expected to find."""
        super().__init__(f"{phase} reported no result named {name!r}")


@dc.dataclass(frozen=True, slots=True)
class Outcome:
    """What one preflight run reported.

    Attributes
    ----------
    exit_code : int
        The process status the run asked for.
    phases : dict[str, dict[str, object]]
        The emitted phase envelopes, keyed by phase name.
    result : str
        The final verdict line's value.
    stdout : str
        Everything the run printed, for assertions on ordering.
    stderr : str
        Everything the run wrote to standard error, where findings and usage
        errors are published.

    """

    exit_code: int
    phases: dict[str, dict[str, object]]
    result: str
    stdout: str
    stderr: str = ""

    def findings(self, phase: str) -> str:
        """Return one phase's findings joined, for substring assertions.

        Parameters
        ----------
        phase : str
            The phase name whose findings are returned.

        Returns
        -------
        str
            The findings, one per line; empty when the phase has none.

        """
        found = self.phases.get(phase, {}).get("findings", [])
        return "\n".join(str(item) for item in found)

    def evidence(self, phase: str, name: str) -> dict[str, object]:
        """Return the evidence one named result left in a phase's detail table.

        Parameters
        ----------
        phase : str
            The phase whose ``results`` are searched.
        name : str
            The declared name of the result to return.

        Returns
        -------
        dict[str, object]
            The result's evidence, as the envelope published it.

        Raises
        ------
        KeyError
            When the phase reported no result under *name*, so a test cannot
            pass by reading another result's evidence.

        """
        results = typ.cast("list[dict[str, object]]", self.detail(phase)["results"])
        for result in results:
            if result["name"] == name:
                return typ.cast("dict[str, object]", result["evidence"])
        raise MissingResultError(phase, name)

    def detail(self, phase: str) -> dict[str, object]:
        """Return one phase's evidence table.

        Parameters
        ----------
        phase : str
            The phase name whose detail is returned.

        Returns
        -------
        dict[str, object]
            The phase's ``detail`` object as the envelope published it.

        """
        return typ.cast("dict[str, object]", self.phases[phase]["detail"])


def outcome_for(
    runner: typ.Callable[..., Outcome],
    root: Path,
    checkout: Checkout | None = None,
    **inputs: str,
) -> Outcome:
    """Write *checkout* at *root* and return what the preflight reported.

    Parameters
    ----------
    runner : Callable[..., Outcome]
        The ``run_preflight`` fixture.
    root : Path
        Where the checkout is written.
    checkout : Checkout | None
        What to write; ``None`` leaves *root* as the test prepared it.
    **inputs : str
        ``INPUT_*`` values for the run.

    Returns
    -------
    Outcome
        The exit status, phases and verdict the run emitted.

    """
    prepared = root if checkout is None else repository(root, checkout)
    return runner(prepared, **inputs)
