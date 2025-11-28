# syspath-hack Users' Guide

The `syspath_hack` package provides helpers for managing `sys.path` entries and
for discovering the project root marker that contains `pyproject.toml` (or any
custom marker). Import the helpers you need from the package root:

```python
from syspath_hack import (
    add_project_root,
    add_to_syspath,
    find_project_root,
    prepend_to_syspath,
    remove_from_syspath,
)
```

## Controlling `sys.path`

- `add_to_syspath(path)` resolves the provided path (accepting `pathlib.Path`
  objects or strings) and appends it to `sys.path` only if the normalised path
  is missing.
- `prepend_to_syspath(path)` performs the same normalisation but ensures the
  path is the first entry in `sys.path`, removing any existing duplicates so
  imports favour that location.
- `remove_from_syspath(path)` performs the inverse operation and removes all
  occurrences of the resolved path from `sys.path`.

Both helpers work with relative paths, paths containing `~`, and existing
entries in `sys.path` that use different but equivalent representations. The
normalisation prevents duplicates when the same location is spelled differently.

## Finding the project root

- `find_project_root(sigil="pyproject.toml")` walks up from the current
  working directory until it finds a file named `sigil`. The search stops at
  the user's home directory (exclusive) and raises `ProjectRootNotFoundError`
  when no marker is found before reaching home or the filesystem root.
- `add_project_root(sigil="pyproject.toml")` combines discovery and path
  mutation by calling `find_project_root` and then `add_to_syspath` with the
  located directory.

Example:

```python
from syspath_hack import add_project_root

add_project_root()  # Ensures the directory with pyproject.toml is on sys.path.
```

The helper returns `None`; it mutates `sys.path` in place so import statements
can resolve modules relative to the project root immediately afterwards.
