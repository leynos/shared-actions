# syspath-hack Users' Guide

The `syspath_hack` package provides helpers for managing `sys.path` entries and
for discovering the project root marker that contains `pyproject.toml` (or any
custom marker). Import the required helpers from the package root:

```python
from syspath_hack import (
    SysPathMode,
    add_project_root,
    add_to_syspath,
    clear_from_syspath,
    ensure_module_dir,
    find_project_root,
    prepend_project_root,
    prepend_to_syspath,
    remove_from_syspath,
    temp_syspath,
)
```

## Controlling `sys.path`

- `add_to_syspath(path)` resolves the provided path (accepting `pathlib.Path`
  objects or strings) and appends it to `sys.path` only if the normalized path
  is missing.
- `prepend_to_syspath(path)` performs the same normalization but ensures the
  path is the first entry in `sys.path`, removing any existing duplicates, so
  imports favour that location. When the list already begins with `""` (the
  interpreter's sentinel meaning "current working directory"), the helper
  preserves that entry even if it resolves to the same directory as the target.
- `remove_from_syspath(path)` performs the inverse operation and removes all
  occurrences of the resolved path from `sys.path`.
- `clear_from_syspath(paths)` accepts an iterable of paths and removes each
  resolved entry in one call, useful for cleaning up multiple test fixtures.

These helpers work with relative paths, paths containing `~`, and existing
entries in `sys.path` that use different but equivalent representations. The
normalization prevents duplicates when the same location is spelled different
ways.

To add paths only temporarily, use `temp_syspath()` as a context manager. It
normalizes and deduplicates the supplied paths, mutates `sys.path`
according to the requested `mode`, and restores the original `sys.path` list
(not just its contents) on exit.

For module-local imports, `ensure_module_dir(__file__, *, mode=...)` resolves
the directory containing the provided `__file__` and adds it to `sys.path` in
one call. It replaces the pattern `Path(__file__).resolve().parent` followed by
a manual prepend or append.

## Finding the project root

- `find_project_root(sigil="pyproject.toml")` walks up from the current
  working directory until it finds a file named `sigil`. The search stops at
  the user's home directory (exclusive) and raises `ProjectRootNotFoundError`
  when no marker is found before reaching home or the filesystem root.
- `add_project_root(sigil="pyproject.toml", *, extra_paths=None)` combines
  discovery and path mutation by adding the located directory to `sys.path`.
  When `extra_paths` is provided, each existing relative or absolute path is
  added after the root.
- `prepend_project_root(sigil="pyproject.toml", *, extra_paths=None)` performs
  the same search but moves the located project root to the front of
  `sys.path`. Any existing `extra_paths` are prepended just behind the root so
  it retains the highest precedence.
- `append_action_root()` / `prepend_action_root()` are shortcuts that search
  for `action.yml` (GitHub Actions) and automatically include `scripts` and
  `src` when those directories exist.

Example:

```python
from syspath_hack import add_project_root

add_project_root()  # Ensures the directory with pyproject.toml is on sys.path.
```

The helper returns `None`; it mutates `sys.path` in place so import statements
can resolve modules relative to the project root immediately afterwards.

## Choosing how paths are added

`SysPathMode` is a `StrEnum` controlling how helpers alter `sys.path`:

- `SysPathMode.PREPEND` places paths at the start of `sys.path`.
- `SysPathMode.APPEND` places them at the end.

Pass either value, or combine them with `|` to perform both operations in
order, typically to guarantee the path ends up first.
