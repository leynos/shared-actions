"""Extract a zip archive, for runners without bsdtar.

`unzip` is not present on the hosted images and GNU tar cannot read a zip, so
the Windows system bsdtar is preferred and this is the fallback. Member paths
are checked before anything is written, because a zip can name a path that
escapes the directory it is extracted into and the standard library's
`extractall` is not obliged to stop it.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def safe_members(archive: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    """Return the archive's members, refusing any that escape the destination."""
    resolved_destination = destination.resolve()
    members = []
    for member in archive.infolist():
        target = (resolved_destination / member.filename).resolve()
        if (
            target != resolved_destination
            and resolved_destination not in target.parents
        ):
            message = f"{member.filename} escapes the extraction directory"
            raise ValueError(message)
        members.append(member)
    return members


def main(argv: list[str]) -> int:
    """Extract the archive named by the arguments into the destination."""
    if len(argv) != 3:
        print(f"usage: {argv[0]} <archive.zip> <destination>", file=sys.stderr)
        return 2
    archive_path = Path(argv[1])
    destination = Path(argv[2])
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = safe_members(archive, destination)
        for member in members:
            archive.extract(member, destination)
            # Zip carries the mode in the high bits of external_attr, and
            # extract() drops it, so an executable arrives without its bit.
            mode = member.external_attr >> 16
            if mode:
                (destination / member.filename).chmod(mode & 0o777)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
