"""Reading ``uv.lock``'s package records, telling a refusal from absence."""

from __future__ import annotations

import typing as typ

from .file_inspection import inspect_path, read_toml

if typ.TYPE_CHECKING:
    from pathlib import Path


class LockRecords:
    """Reads the packages a checkout's ``uv.lock`` records."""

    def read_lock_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read."""
        lock = root / "uv.lock"
        present, regular, problem = inspect_path(lock)
        if problem is not None:
            return [], problem
        if not present:
            return [], "uv.lock is absent, so no locked revision can be established"
        if not regular:
            return [], (
                "uv.lock exists but is not a file, so no locked revision can be "
                "established"
            )
        document, problem = read_toml(lock)
        if problem is not None:
            return [], problem
        return self._package_records(document or {})

    @staticmethod
    def _package_records(
        document: dict[str, object],
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return the lock's package tables, or why its shape is unusable.

        A record that is not a table is skipped: the lock's own shape has
        already been refused if the entry is not an array, so what is left is
        noise a later uv may add.
        """
        packages = document.get("package", [])
        if not isinstance(packages, list):
            return [], "uv.lock's package entry is not an array"
        records = typ.cast("list[object]", packages)
        tables = [item for item in records if isinstance(item, dict)]
        return [typ.cast("dict[str, object]", item) for item in tables], None
