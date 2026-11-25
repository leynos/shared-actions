"""Loading the shared requires-python rule the interpreter phases judge with.

The rule is the closed cv005-contracts one (#565), loaded by path from this
checkout rather than imported as a package: the caller's environment holds the
caller's dependencies, not this repository's.
"""

from __future__ import annotations

import importlib.util
import typing as typ

from .constants import SHARED_RULE

if typ.TYPE_CHECKING:
    from .ports import RequirementRule


def load_shared_rule() -> tuple[RequirementRule | None, str]:
    """Return the shared interpreter rule (#565), or why it is unavailable.

    It is loaded by path rather than imported as a package: the caller's
    environment holds the caller's dependencies, not this repository's, and
    importing ``cv005_contracts`` would pull its whole contract graph in for
    one pure function. Loading is an adapter concern, so it lives here rather
    than in the phase that judges the rule's answers.

    Returns
    -------
    tuple[RequirementRule | None, str]
        The rule, and the reason it is absent when it could not be loaded. The
        rule is loaded once at the command line and carried in the run, so no
        phase below it re-reads the checkout to find it.

    """
    if not SHARED_RULE.is_file():
        return None, f"the shared requires-python rule is absent: {SHARED_RULE}"
    spec = importlib.util.spec_from_file_location(
        "_mutation_preflight_requires_python", SHARED_RULE
    )
    if spec is None or spec.loader is None:
        absent = f"the shared requires-python rule could not be loaded: {SHARED_RULE}"
        return None, absent
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as error:
        return None, f"the shared requires-python rule failed to load: {error}"
    rule = getattr(module, "requirement_violations", None)
    if rule is None:
        return None, (
            f"the shared rule at {SHARED_RULE} no longer exposes requirement_violations"
        )
    return rule, ""
