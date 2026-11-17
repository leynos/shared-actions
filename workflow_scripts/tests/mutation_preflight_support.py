"""Checkout builders and run outcomes shared by the mutation preflight tests.

One builder writes a mutation-ready checkout, and one dataclass groups the
knobs a test turns. Keeping them here means each phase test reads as a
statement about a declaration rather than about how a checkout is assembled.
"""

from __future__ import annotations

import dataclasses as dc
import sys
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from pathlib import Path

# The fake tool shims are POSIX shell scripts; the reusable workflows only run
# on ubuntu-latest, and on Windows the real tools would answer instead.
POSIX_SHIMS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="fake tool helpers emit POSIX sh"
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
    """Write *text* at *relative* under *root*, creating parent directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repository(root: Path, checkout: Checkout | None = None) -> Path:
    """Write a mutation-ready checkout and return its root."""
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
    """Return a ``uv.lock`` pinning one package at *version* from *source*."""
    return (
        "version = 1\n\n"
        "[[package]]\n"
        'name = "example"\nversion = "0.1.0"\n\n'
        "[[package]]\n"
        'name = "tei-rapporteur"\n'
        f'version = "{version}"\n'
        f"source = {{ {source} }}\n"
    )


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

    """

    exit_code: int
    phases: dict[str, dict[str, object]]
    result: str
    stdout: str

    def findings(self, phase: str) -> str:
        """Return one phase's findings joined, for substring assertions."""
        found = self.phases.get(phase, {}).get("findings", [])
        return "\n".join(str(item) for item in found)

    def detail(self, phase: str) -> dict[str, object]:
        """Return one phase's evidence table."""
        return typ.cast("dict[str, object]", self.phases[phase]["detail"])
