"""The composition point for the preflight's filesystem adapters.

Each adapter lives beside the concern it serves: ``documents.py`` reads the
documents a declaration may live in, ``lock_records.py`` reads the lock's
package records, ``mutmut_configuration.py`` reads the mutation tool's
configuration, ``checkout_paths.py`` locates declared paths, ``sandbox_files.py``
builds and removes the sandbox a check runs in, ``evidence.py`` writes what the
run publishes, and ``shared_rule.py`` loads the interpreter rule. This module
only gathers them, so the command line imports one place and no reader has to
know which module holds which adapter.
"""

from __future__ import annotations

from .checkout_paths import CheckoutPaths
from .documents import DeclarationDocuments
from .evidence import EvidenceFiles
from .lock_records import LockRecords
from .mutmut_configuration import MutmutConfiguration
from .sandbox_files import SandboxMaterializer
from .shared_rule import load_shared_rule

__all__ = [
    "CheckoutPaths",
    "DeclarationDocuments",
    "EvidenceFiles",
    "LockRecords",
    "MutmutConfiguration",
    "SandboxMaterializer",
    "load_shared_rule",
]
