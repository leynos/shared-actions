"""Unpack a zip archive, stripping its single top-level directory.

Last-resort arm of the action's zip extraction. The Windows system `tar.exe`
is bsdtar, reads zip and honours ``--strip-components``, so it is tried first
and handles the platform that actually ships zip assets. It does not exist off
Windows, though, and the action must not depend on `unzip`, which some Windows
runner images lack. Every GitHub-hosted and Ubicloud image ships Python 3, so
this keeps the zip path working on a runner that offers neither.

The behaviour matches `tar -xf <archive> --strip-components=1 -C <destination>`
for the shape the Whitaker release actually has: one top-level directory
holding the installer executable. A member outside that directory, or a member
whose path escapes the destination, is refused rather than written, because a
release archive is fetched over the network and this runs before anything has
inspected what it contains.
"""

from __future__ import annotations

import pathlib
import sys
import zipfile

STRIP_COMPONENTS = 1


def _reject_unsafe_name(name: str) -> pathlib.PurePosixPath:
    """Parse a member name, refusing one that is absolute or parent-relative.

    Parameters
    ----------
    name
        The member's archive path, which zip always spells with forward
        slashes regardless of the platform that wrote it.

    Returns
    -------
    pathlib.PurePosixPath
        The parsed member path.

    Raises
    ------
    ValueError
        If the name is absolute or begins with a parent reference.
    """
    pure = pathlib.PurePosixPath(name)
    leads_upward = bool(pure.parts) and pure.parts[0] == ".."
    if pure.is_absolute() or leads_upward:
        message = f"refusing to extract absolute or parent-relative member {name!r}"
        raise ValueError(message)
    return pure


def _resolve_within(
    target: pathlib.Path,
    destination: pathlib.Path,
    name: str,
) -> pathlib.Path:
    """Resolve a member's output path, refusing one outside ``destination``.

    Parameters
    ----------
    target
        The unresolved output path.
    destination
        Directory the archive is being unpacked into.
    name
        The member's archive path, for the error message.

    Returns
    -------
    pathlib.Path
        The resolved output path.

    Raises
    ------
    ValueError
        If the resolved path lies outside ``destination``.
    """
    resolved = target.resolve()
    root = destination.resolve()
    if resolved != root and root not in resolved.parents:
        message = f"refusing to extract {name!r} outside the destination"
        raise ValueError(message)
    return resolved


def stripped_target(
    name: str,
    destination: pathlib.Path,
) -> pathlib.Path | None:
    """Return where a zip member lands once its leading component is stripped.

    Parameters
    ----------
    name
        The member's archive path.
    destination
        Directory the archive is being unpacked into.

    Returns
    -------
    pathlib.Path or None
        The resolved output path, or ``None`` when the member has nothing left
        after stripping and so contributes no file.

    Raises
    ------
    ValueError
        If the member is absolute, or if its stripped path escapes
        ``destination``. Both indicate an archive that is not the one this
        action expects, so extraction stops rather than writing outside the
        staging directory.
    """
    pure = _reject_unsafe_name(name)
    parts = pure.parts[STRIP_COMPONENTS:]
    if not parts:
        return None
    return _resolve_within(destination.joinpath(*parts), destination, name)


def extract(archive: pathlib.Path, destination: pathlib.Path) -> int:
    """Unpack ``archive`` into ``destination``, stripping one path component.

    Parameters
    ----------
    archive
        Path to the zip file.
    destination
        Directory to unpack into. It must already exist.

    Returns
    -------
    int
        The number of files written.
    """
    written = 0
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            if info.is_dir():
                continue
            target = stripped_target(info.filename, destination)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(info))
            # The installer must be executable. zip carries Unix permissions
            # only when the writer chose to record them, so they are set here
            # rather than read back out of the archive.
            target.chmod(0o755)
            written += 1
    return written


def main(argv: list[str]) -> int:
    """Run the extractor from the command line.

    Parameters
    ----------
    argv
        The arguments after the program name: exactly two, the path to the
        zip archive and the path to an existing destination directory. The
        action's Bash arm passes both as absolute paths.

    Returns
    -------
    int
        The process exit status.

        ``0``
            Extraction succeeded and wrote at least one file.
        ``1``
            Extraction failed. The archive was unreadable or not a zip, a
            member would have escaped the destination, or the archive held no
            files below its top-level directory. An archive that yields
            nothing is an error rather than a silent success, because exiting
            zero would leave the install step reporting a missing executable
            instead of a bad archive.
        ``2``
            The wrong number of arguments, which is a caller error rather
            than an extraction failure and is distinguished so the two do not
            look alike in a job log.

    Notes
    -----
    Every failure is reported as a GitHub Actions error annotation on standard
    error rather than as a traceback, so the job log names the cause.
    """
    if len(argv) != 2:
        print(
            "usage: extract-zip.py <archive> <destination>",
            file=sys.stderr,
        )
        return 2
    archive = pathlib.Path(argv[0])
    destination = pathlib.Path(argv[1])
    try:
        written = extract(archive, destination)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(
            f"::error title=Whitaker installer failed::{error}",
            file=sys.stderr,
        )
        return 1
    if written == 0:
        print(
            "::error title=Whitaker installer failed::the zip asset held no "
            "files below its top-level directory",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
