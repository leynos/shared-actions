"""Validation and resolution of the `bin-dir` input.

`bin-dir` is appended to `GITHUB_PATH` and used as a cache path, so it is
checked before use: absolute or `~/`-relative, no newline that could smuggle
an extra output record, no parent-directory component, and no PATH separator.
Resolution is a query and creates nothing; the `install` step makes the
directory when it writes the binary.
"""

from __future__ import annotations

from pathlib import Path

from makeutil_errors import InvalidInputError

_MAX_BIN_DIR_LENGTH = 240


def _reject_crlf_in_bin_dir(bin_dir_input: str) -> None:
    """Reject a `bin-dir` containing a carriage return or newline."""
    if "\r" in bin_dir_input or "\n" in bin_dir_input:
        msg = "bin-dir must not contain a carriage return or newline"
        raise InvalidInputError(msg)


def _reject_overlong_bin_dir(bin_dir_input: str) -> None:
    """Reject a `bin-dir` longer than the runner-safe ceiling."""
    if len(bin_dir_input) > _MAX_BIN_DIR_LENGTH:
        msg = f"bin-dir must be at most {_MAX_BIN_DIR_LENGTH} characters"
        raise InvalidInputError(msg)


def _expand_bin_dir(bin_dir_input: str) -> str:
    """Expand an absolute or `~/`-relative `bin-dir` to a plain path string."""
    if bin_dir_input == "~" or bin_dir_input.startswith("~/"):
        return str(Path.home()) + bin_dir_input[1:]
    if bin_dir_input.startswith("/"):
        return bin_dir_input
    msg = "bin-dir must be an absolute path or start with ~/"
    raise InvalidInputError(msg)


def _reject_parent_components(expanded_bin_dir: str) -> None:
    """Reject an expanded `bin-dir` containing a parent-directory component."""
    if "/../" in f"/{expanded_bin_dir}/":
        msg = "bin-dir must not contain parent-directory components"
        raise InvalidInputError(msg)


def _reject_path_separator(expanded_bin_dir: str) -> None:
    """Reject an expanded `bin-dir` containing the runner PATH separator."""
    if ":" in expanded_bin_dir:
        msg = "bin-dir must not contain the runner PATH separator"
        raise InvalidInputError(msg)


def _absolute_bin_dir(expanded_bin_dir: str) -> Path:
    """Return the expanded `bin-dir` as an absolute path, symlinks resolved.

    Nothing is created: resolving is a query, and the install step makes the
    directory when it writes the binary. A path that does not exist yet
    resolves as far as it does exist.
    """
    return Path(expanded_bin_dir).resolve()


def _revalidate_resolved_bin_dir(resolved_bin_dir: Path) -> None:
    """Reapply the CR/LF, PATH-separator and parent-component checks to a
    resolved `bin-dir`.

    `bin_dir_input` is validated only in its given spelling; when it is a
    symlink, the resolved target - what is actually published to
    `GITHUB_PATH` - could still smuggle a rejected character or component.
    Re-running the same checks against the resolved path closes that gap.
    """
    resolved_str = str(resolved_bin_dir)
    _reject_crlf_in_bin_dir(resolved_str)
    _reject_parent_components(resolved_str)
    _reject_path_separator(resolved_str)


def resolve_bin_dir(bin_dir_input: str) -> Path:
    """Validate `bin_dir_input` and return it as an absolute path.

    The directory is not created here; the `install` subcommand creates it.

    Parameters
    ----------
    bin_dir_input : str
        The raw `bin-dir` input: an absolute path, or one starting `~/`.

    Returns
    -------
    Path
        The resolved directory, which may not exist yet.

    Raises
    ------
    InvalidInputError
        If the input is malformed.
    """
    _reject_crlf_in_bin_dir(bin_dir_input)
    _reject_overlong_bin_dir(bin_dir_input)
    expanded_bin_dir = _expand_bin_dir(bin_dir_input)
    _reject_parent_components(expanded_bin_dir)
    _reject_path_separator(expanded_bin_dir)
    resolved_bin_dir = _absolute_bin_dir(expanded_bin_dir)
    _revalidate_resolved_bin_dir(resolved_bin_dir)
    return resolved_bin_dir
