"""Reaching the shared ``emit`` from either import root this helper can have.

``uv run --script`` puts ``workflow_scripts/`` on the path and imports the
package as ``mutationpreflight``; the test suite imports it as
``workflow_scripts.mutationpreflight``. Both names reach the same ``output``
module, so the choice is made once here rather than in every phase.
"""

from __future__ import annotations

if __package__ and "." in __package__:
    # Relative, so a package nested under a dotted parent still reaches the
    # ``output`` module beside ``workflow_scripts`` rather than guessing its
    # top-level name.
    from ..output import emit  # noqa: TID252 - the parent package name is not fixed
else:
    from output import emit  # type: ignore[import-not-found]

__all__ = ["emit"]
