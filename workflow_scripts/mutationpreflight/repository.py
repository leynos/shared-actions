"""The adapter that reads and writes the checkout the preflight judges.

Every path a phase asks about, every document it parses and every artefact it
publishes passes through this module. The phases hold no path of their own
beyond the root they were started for, so a stand-in that refuses every
question here is enough to show that a phase reads nothing without passing
through the boundary.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import tomllib
import typing as typ
from configparser import ConfigParser
from configparser import Error as ConfigError
from pathlib import Path

from .constants import EXCLUDED_DIRECTORIES, SHARED_RULE
from .ports import Document, LocatedPath

if typ.TYPE_CHECKING:
    from .copy_set import CopyContract
    from .models import Ran
    from .ports import RequirementRule

#: Why the mutmut configuration is unknown: neither file that could hold it is
#: present. An unreadable file is reported with its own problem instead.
_NO_CONFIG: typ.Final[str] = (
    "neither pyproject.toml's [tool.mutmut] nor setup.cfg's [mutmut] "
    "is present, so what mutmut copies cannot be established"
)


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


class FileRepository:
    """The repository port, answered by the operating system's filesystem.

    Every method reads or writes the real checkout. Nothing is cached: a phase
    that asks twice sees what is on disk at the second question, which is what
    lets the native phase notice a lock a declared check has rewritten. The
    reading and copying helpers are methods rather than module functions so the
    port's implementation is one unit and every caller of it is inside it.
    """

    def read_documents(
        self, root: Path, relatives: tuple[Path, ...]
    ) -> tuple[Document, ...]:
        """Return every one of *relatives* present under *root*, parsed in order."""
        documents: list[Document] = []
        for relative in relatives:
            path = root / relative
            if not path.is_file():
                continue
            document, problem = self._read_document(path)
            documents.append(Document(path, document, problem or ""))
        return tuple(documents)

    def read_lock_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read."""
        packages, problem = self._locked_packages(root)
        return packages, problem

    def read_mutmut_configuration(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        table, problem = self._mutmut_table(root)
        return table, problem

    def locate(
        self, root: Path, declared: str
    ) -> tuple[LocatedPath | None, str | None]:
        """Return what *declared* is under *root*, or why it could not be resolved."""
        try:
            anchor = root.resolve()
            resolved = (anchor / declared).resolve()
        except OSError as error:
            return None, f"{declared!r} could not be resolved: {error}"
        inside = resolved.is_relative_to(anchor)
        return (
            LocatedPath(
                exists=(root / declared).exists(),
                resolved=str(resolved),
                inside_root=inside,
                parts=resolved.relative_to(anchor).parts if inside else (),
            ),
            None,
        )

    def is_directory(self, path: Path) -> bool:
        """Return whether *path* is a directory."""
        return path.is_dir()

    def materialize(
        self, root: Path, contract: CopyContract
    ) -> tuple[Path | None, tuple[str, ...]]:
        """Copy *contract* into a fresh directory, returning it or why not."""
        try:
            destination = Path(tempfile.mkdtemp(prefix="mutation-preflight-sandbox-"))
        except OSError as error:
            return None, (f"the check sandbox could not be created: {error}",)
        try:
            anchor = root.resolve()
            for entry in contract.paths:
                self._copy_entry(anchor, root, destination, entry)
            for pattern in contract.globs:
                for source in sorted(root.glob(pattern)):
                    if source.is_file():
                        # A glob match receives the same containment rule as a
                        # declared entry, so an escaping link stays a link.
                        self._copy_source(
                            anchor, destination, source, destination / source.name
                        )
        except OSError as error:
            shutil.rmtree(destination, ignore_errors=True)
            return None, (f"the check sandbox could not be materialized: {error}",)
        return destination, ()

    def discard(self, sandbox: Path) -> None:
        """Remove a materialized sandbox once the checks have run."""
        shutil.rmtree(sandbox, ignore_errors=True)

    def write_check_logs(
        self, directory: Path, stem: str, name: str, outcome: Ran
    ) -> str | None:
        """Write one check's bounded streams, returning why they were not written."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{stem}.stdout.log").write_text(
                outcome.stdout, encoding="utf-8"
            )
            (directory / f"{stem}.stderr.log").write_text(
                outcome.stderr, encoding="utf-8"
            )
        except OSError as error:
            return f"check {name!r}'s logs could not be written: {error}"
        return None

    def append_summary(self, path: Path, rendered: str) -> str | None:
        """Append the rendered summary, returning why it could not be written."""
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(rendered)
        except OSError as error:
            return f"the job summary {str(path)!r} could not be written: {error}"
        return None

    def write_report(self, output_dir: Path, document: dict[str, object]) -> str | None:
        """Write the machine-readable report, returning why it was not written."""
        destination = output_dir / "mutation-preflight.json"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            return f"the report {str(destination)!r} could not be written: {error}"
        return None

    def _read_document(self, path: Path) -> tuple[dict[str, object] | None, str | None]:
        """Return *path* parsed as TOML, or why it could not be read."""
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            return None, f"{path} could not be read: {error}"
        return parsed, None

    def _locked_packages(
        self, root: Path
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return ``uv.lock``'s package records, or why they could not be read."""
        lock = root / "uv.lock"
        if not lock.is_file():
            return [], "uv.lock is absent, so no locked revision can be established"
        document, problem = self._read_document(lock)
        if problem is not None:
            return [], problem
        packages = (document or {}).get("package", [])
        if not isinstance(packages, list):
            return [], "uv.lock's package entry is not an array"
        records = typ.cast("list[object]", packages)
        tables = [item for item in records if isinstance(item, dict)]
        return [typ.cast("dict[str, object]", item) for item in tables], None

    def _flattened_section(self, parser: ConfigParser) -> dict[str, object] | None:
        """Return the parsed ``[mutmut]`` section, or ``None`` when it is absent."""
        if not parser.has_section("mutmut"):
            return None
        section = parser["mutmut"]
        table: dict[str, object] = {}
        for key in ("source_paths", "paths_to_mutate", "also_copy"):
            if key not in section:
                continue
            raw = section[key]
            # mutmut splits a multi-line value on newlines and wraps a single
            # line as one entry, so a comma-separated line is one path too.
            table[key] = (
                [item for item in raw.split("\n") if item] if "\n" in raw else [raw]
            )
        return table

    def _setup_cfg_table(
        self, root: Path
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return ``setup.cfg``'s ``[mutmut]`` section, or why it went unread."""
        path = root / "setup.cfg"
        if not path.is_file():
            return None, None
        parser = ConfigParser()
        try:
            # Reading a value can raise InterpolationSyntaxError, a ConfigError.
            parser.read(path, encoding="utf-8")
            table = self._flattened_section(parser)
        except (OSError, UnicodeDecodeError, ConfigError) as error:
            return None, f"{path} could not be read: {error}"
        return table, None

    def _mutmut_table(self, root: Path) -> tuple[dict[str, object] | None, str | None]:
        """Return the mutmut configuration the run will use, or why it is unknown."""
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            document, problem = self._read_document(pyproject)
            if problem is not None:
                return None, problem
            table = self._nested_table(document or {}, "tool", "mutmut")
            if table is not None:
                return table, None
        table, problem = self._setup_cfg_table(root)
        if table is not None:
            return table, None
        if problem is not None:
            # An unreadable setup.cfg is not the same fact as one without a
            # [mutmut] section, and saying the section is absent would mislead.
            return None, problem
        return None, _NO_CONFIG

    def _nested_table(
        self, document: dict[str, object], *keys: str
    ) -> dict[str, object] | None:
        """Return the table at *keys*, or ``None`` when the path is not a table."""
        current: object = document
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return None
            current = typ.cast("dict[str, object]", current)[key]
        return (
            typ.cast("dict[str, object]", current)
            if isinstance(current, dict)
            else None
        )

    def _escapes(self, root: Path, source: Path) -> bool:
        """Return whether *source* is a link whose target leaves *root*."""
        return source.is_symlink() and not source.resolve().is_relative_to(root)

    def _keep_link(self, source: Path, target: Path) -> None:
        """Write *source*'s link at *target*, naming the target it already had."""
        target.symlink_to(source.readlink(), target_is_directory=source.is_dir())

    def _keep_link_unless_present(self, source: Path, target: Path) -> None:
        """Write *source*'s link at *target* unless the path is already written.

        Overlapping contract entries reach the same sandbox path: the default
        ``tests`` entry and an ``also_copy`` entry naming a link inside it both
        copy that link. The first write is the one that counts, so a second is
        left alone rather than raising on the existing path.
        """
        if target.is_symlink() or target.exists():
            return
        self._keep_link(source, target)

    def _kept_as_link(self, root: Path, source: Path, seen: frozenset[Path]) -> bool:
        """Return whether *source* must be copied as a link rather than followed."""
        if not source.is_symlink():
            return False
        if self._escapes(root, source):
            return True
        return source.resolve() in seen

    def _escaping_ancestor(self, root: Path, source: Path) -> Path | None:
        """Return the nearest ancestor of *source* that links out of *root*."""
        for ancestor in source.parents:
            if ancestor == root or not ancestor.is_relative_to(root):
                return None
            if self._escapes(root, ancestor):
                return ancestor
        return None

    def _keep_link_at(self, destination: Path, root: Path, link: Path) -> None:
        """Write *link* into the sandbox where it sits in the checkout."""
        target = destination / link.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._keep_link_unless_present(link, target)

    def _copy_source(
        self, root: Path, destination: Path, source: Path, target: Path
    ) -> None:
        """Copy *source* to *target*, keeping an escaping ancestor as a link."""
        escape = self._escaping_ancestor(root, source)
        if escape is not None:
            self._keep_link_at(destination, root, escape)
            return
        self._copy_path(root, source, target, frozenset())

    def _copy_path(
        self, root: Path, source: Path, target: Path, seen: frozenset[Path]
    ) -> None:
        """Copy one entry, applying the containment rule at every level.

        A symlink whose target is inside the checkout is materialized by
        content, because that is what mutmut's ``copytree`` does: it follows
        links and copies what they point at, so a fixture tree linked to a
        sibling directory arrives complete rather than dangling. A link that
        leaves the checkout is kept as a link wherever it appears, so a
        directory copied whole cannot import a tree the declaration never
        named. A link that would re-enter a directory already being copied is
        kept as a link as well, so a cycle is preserved rather than followed
        forever.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._kept_as_link(root, source, seen):
            self._keep_link_unless_present(source, target)
            return
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            inner = seen | {source.resolve()}
            for child in sorted(source.iterdir()):
                if child.name in EXCLUDED_DIRECTORIES:
                    continue
                self._copy_path(root, child, target / child.name, inner)
            return
        shutil.copy2(source, target)

    def _copy_entry(
        self, root: Path, repo_root: Path, destination: Path, entry: str
    ) -> None:
        """Copy one contract entry, keeping its place in the tree."""
        source = repo_root / entry
        if not source.exists() and not source.is_symlink():
            return
        self._copy_source(root, destination, source, destination / entry)
