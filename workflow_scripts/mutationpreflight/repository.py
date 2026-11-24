"""The composition point for the preflight's filesystem adapters.

Each adapter lives beside the concern it serves: ``documents.py`` reads the
files a judgement parses, ``sandbox_files.py`` builds and removes the sandbox a
check runs in, ``evidence.py`` writes what the run publishes, and
``shared_rule.py`` loads the interpreter rule. This module only gathers them,
so the command line imports one place and no reader has to know which module
holds which adapter.
"""

from __future__ import annotations

from .documents import FileDocuments
from .evidence import EvidenceFiles
from .sandbox_files import SandboxMaterializer
from .shared_rule import load_shared_rule

__all__ = [
    "EvidenceFiles",
    "FileDocuments",
    "SandboxMaterializer",
    "load_shared_rule",
]
