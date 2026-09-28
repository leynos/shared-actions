"""Read a repository's CV-005 parameters from `.github/cv005.toml`.

Everything that is estate policy lives in the rules. This file holds only
what differs between repositories, and an unknown key is refused rather than
ignored, so a misspelt parameter cannot silently fall back to its default.
"""

from __future__ import annotations

import dataclasses as dc
import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

#: Where a repository keeps its parameters, relative to its root.
CONFIG_PATH: typ.Final[str] = ".github/cv005.toml"


class ConfigError(ValueError):
    """Raised when `.github/cv005.toml` is missing, malformed or unknown."""


@dc.dataclass(frozen=True, slots=True)
class Config:
    """One repository's CV-005 parameters.

    Attributes
    ----------
    repository : str
        The owner and name, such as `leynos/example`, used to refuse this
        repository's own workflows and actions named at a ref.
    publisher : str
        The publisher workflow's file name.
    interpreter : str | None
        The Python version every generator must pin through `UV_PYTHON`, or
        None where the repository does not measure Python.
    environment : bool
        Whether the `codescene` environment contract applies.
    selection : dict[str, str]
        Generator inputs the publisher must carry with exactly these values.

    """

    repository: str
    publisher: str = "coverage-main.yml"
    interpreter: str | None = None
    environment: bool = True
    selection: dict[str, str] = dc.field(default_factory=dict)


def load_config(repo_root: Path) -> Config:
    """Read `.github/cv005.toml` under a repository root.

    Parameters
    ----------
    repo_root : Path
        The repository root.

    Returns
    -------
    Config
        The parameters, with defaults for any key the file omits.

    Raises
    ------
    ConfigError
        If the file is missing or not TOML, names an unknown key, omits
        `repository`, or gives a value of the wrong type.

    """
    path = repo_root / CONFIG_PATH
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        message = f"{CONFIG_PATH} could not be read: {error}"
        raise ConfigError(message) from error
    return config_from_mapping(raw)


def config_from_mapping(raw: dict[str, object]) -> Config:
    """Build a `Config` from parsed TOML, refusing anything it does not know.

    Parameters
    ----------
    raw : dict[str, object]
        The parsed file.

    Returns
    -------
    Config
        The validated parameters.

    Raises
    ------
    ConfigError
        If a key is unknown, `repository` is missing, or a value has the
        wrong type.

    Examples
    --------
    >>> config_from_mapping({"repository": "leynos/example"}).publisher
    'coverage-main.yml'

    """
    known = {field.name for field in dc.fields(Config)}
    if unknown := sorted(set(raw) - known):
        message = f"{CONFIG_PATH} names unknown keys: {unknown}"
        raise ConfigError(message)
    repository = raw.get("repository")
    if not isinstance(repository, str) or repository.count("/") != 1:
        message = f"{CONFIG_PATH} must set repository = 'owner/name'"
        raise ConfigError(message)
    _require(raw, "publisher", str)
    _require(raw, "interpreter", str)
    _require(raw, "environment", bool)
    _require(raw, "selection", dict)
    selection = raw.get("selection", {})
    if not all(
        isinstance(value, str) for value in typ.cast("dict", selection).values()
    ):
        message = f"{CONFIG_PATH} selection values must be strings"
        raise ConfigError(message)
    return Config(**typ.cast("dict[str, typ.Any]", raw))


def _require(raw: dict[str, object], key: str, kind: type) -> None:
    """Refuse a present key whose value is not of the expected type."""
    if key in raw and not isinstance(raw[key], kind):
        message = f"{CONFIG_PATH} {key} must be a {kind.__name__}"
        raise ConfigError(message)
