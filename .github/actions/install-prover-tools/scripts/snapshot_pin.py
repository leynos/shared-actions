"""Check that a consumer's Kani snapshot pin can be a version 1 pin.

The action only names a file that cannot be one; ``prover-tools`` validates the
values (tag grammar, digests, commits) when it installs.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

SNAPSHOT_PIN = "tools/kani/snapshot.toml"
_PIN_KEYS = frozenset(
    {
        "schema_version",
        "repository",
        "tag",
        "upstream_commit",
        "builder_commit",
        "target",
        "manifest_sha256",
    }
)


class PinError(ValueError):
    """A pin the action refuses, with the file named in the message."""


def _load(pin: Path) -> dict[str, object]:
    """Read and parse *pin*, or raise ``PinError`` naming it."""
    # Imported here so a job with no pin runs on an interpreter older than 3.11.
    try:
        import tomllib
    except ImportError:
        message = f"{pin} needs Python 3.11 or newer on the runner to be checked"
        raise PinError(message) from None

    try:
        return tomllib.loads(pin.read_bytes().decode("utf-8"))
    except OSError as error:
        message = f"cannot read {pin}: {error.strerror}"
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        message = f"{pin} is not valid TOML: {error}"
    raise PinError(message)


def check_pin(pin: Path) -> None:
    """Raise ``PinError`` unless *pin* holds exactly the version 1 keys."""
    document = _load(pin)
    keys = frozenset(document)
    if keys != _PIN_KEYS:
        missing = ", ".join(sorted(_PIN_KEYS - keys)) or "none"
        unknown = ", ".join(sorted(keys - _PIN_KEYS)) or "none"
        message = f"{pin} is not a snapshot pin: missing {missing}; unknown {unknown}"
        raise PinError(message)
    version = document["schema_version"]
    if version != 1 or isinstance(version, bool):
        message = f"{pin} has schema_version {version!r}; need 1"
        raise PinError(message)
