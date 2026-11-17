"""Reaching the shared ``emit`` from either import root this helper can have.

``uv run --script`` puts ``workflow_scripts/`` on the path and imports the
package as ``mutationpreflight``; the test suite imports it as
``workflow_scripts.mutationpreflight``. Both names reach the same ``output``
module, so the choice is made once here rather than in every phase.
"""

from __future__ import annotations

if __package__ and "." in __package__:
    from workflow_scripts.output import emit
else:
    from output import emit  # type: ignore[import-not-found]

__all__ = ["emit"]
