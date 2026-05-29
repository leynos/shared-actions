"""Configuration models and loader for the staging helper.

This module provides dataclasses and a loader function for parsing TOML staging
configurations that describe artefact sources, target platforms, and staging
directory templates.
"""

from __future__ import annotations

import dataclasses
import hashlib
import tomllib
import typing as typ
from pathlib import Path

from .errors import StageError

_T = typ.TypeVar("_T")

__all__ = [
    "ArtefactConfig",
    "BinstallConfig",
    "StagingConfig",
    "load_config",
]


@dataclasses.dataclass(slots=True)
class ArtefactConfig:
    """Describe a single artefact to be staged."""

    source: str
    required: bool = True
    output: str | None = None
    destination: str | None = None
    alternatives: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(slots=True)
class BinstallConfig:
    """Optional cargo-binstall archive generation settings.

    Describe the inputs and template defaults used by the staging pipeline
    when packaging a release binary into a ``cargo-binstall``-compatible
    archive alongside the standard staged artefacts.

    Parameters
    ----------
    enabled : bool, default False
        Toggle cargo-binstall archive generation for this target.
    manifest_path : str, default "Cargo.toml"
        Cargo manifest path relative to the workspace, or an absolute path.
        Used to resolve the package name and version when they are not
        provided explicitly.
    package_name : str or None, default None
        Override the package name from the Cargo manifest.
    version : str or None, default None
        Override the package version from the Cargo manifest.
    bin_name : str or None, default None
        Override the binary name resolved from :class:`StagingConfig`.
    archive_name : str, default "{package_name}-{version}-{target}.tar.gz"
        ``str.format`` template for the staged archive file name.
    binary_source : str, default "target/{target}/release/{bin_name}{bin_ext}"
        ``str.format`` template for the host-side binary path that is added
        to the archive.
    binary_name : str, default "{bin_name}{bin_ext}"
        ``str.format`` template for the archive member name of the binary.
    output : str, default "binstall_archive_path"
        Output key under which the resolved archive path is exposed.

    Notes
    -----
    All template fields are rendered with the staging context produced by
    :meth:`StagingConfig.as_template_context`, extended with
    ``package_name``, ``version``, and ``bin_name``.
    """

    enabled: bool = False
    manifest_path: str = "Cargo.toml"
    package_name: str | None = None
    version: str | None = None
    bin_name: str | None = None
    archive_name: str = "{package_name}-{version}-{target}.tar.gz"
    binary_source: str = "target/{target}/release/{bin_name}{bin_ext}"
    binary_name: str = "{bin_name}{bin_ext}"
    output: str = "binstall_archive_path"


@dataclasses.dataclass(slots=True)
class StagingConfig:
    """Concrete configuration produced by :func:`load_config`."""

    workspace: Path
    bin_name: str
    dist_dir: str
    checksum_algorithm: str
    artefacts: list[ArtefactConfig]
    platform: str
    arch: str
    target: str
    bin_ext: str = ""
    staging_dir_template: str = "{bin_name}_{platform}_{arch}"
    target_key: str | None = None
    binstall: BinstallConfig = dataclasses.field(default_factory=BinstallConfig)

    def staging_dir(self) -> Path:
        """Return the absolute staging directory path."""
        return self.workspace / self.dist_dir / self.staging_dir_name

    @property
    def staging_dir_name(self) -> str:
        """Directory name rendered from :attr:`staging_dir_template`."""
        return self.as_template_context()["staging_dir_name"]

    def as_template_context(self) -> dict[str, typ.Any]:
        """Return a mapping suitable for rendering ``str.format`` templates."""
        base_context: dict[str, typ.Any] = {
            "workspace": self.workspace.as_posix(),
            "bin_name": self.bin_name,
            "dist_dir": self.dist_dir,
            "checksum_algorithm": self.checksum_algorithm,
            "platform": self.platform,
            "arch": self.arch,
            "target": self.target,
            "bin_ext": self.bin_ext or "",
            "target_key": self.target_key or "",
        }
        if self.binstall.package_name is not None:
            base_context["package_name"] = self.binstall.package_name
        if self.binstall.version is not None:
            base_context["version"] = self.binstall.version
        template_context = base_context | {
            "staging_dir_template": self.staging_dir_template
        }
        return template_context | {
            "staging_dir_name": self.staging_dir_template.format(**template_context)
        }


def load_config(
    config_file: Path, target_key: str, *, workspace: Path
) -> StagingConfig:
    """Load staging configuration from ``config_file`` for ``target_key``.

    Parameters
    ----------
    config_file
        Path to the TOML configuration file describing staging inputs.
    target_key
        Key identifying the target-specific configuration section to load.
    workspace
        Mandatory workspace root supplied by the caller.

    Returns
    -------
    StagingConfig
        Fully realised configuration containing resolved paths and artefacts.

    Raises
    ------
    FileNotFoundError
        Raised when the configuration file is absent at ``config_file``.
    StageError
        Raised when required configuration keys are missing or invalid.
    """
    config_file = Path(config_file)
    if not config_file.is_file():
        msg = f"Configuration file not found at {config_file}"
        raise FileNotFoundError(msg)

    data = _load_toml(config_file)
    common, target_cfg = _extract_sections(data, config_file, target_key)
    _require_keys(
        target_cfg,
        {"platform", "arch", "target"},
        f"targets.{target_key}",
        config_file,
    )
    algorithm = _validate_checksum(common.get("checksum_algorithm"))
    binstall = _make_binstall_config(common, target_cfg, config_file)
    bin_name = common.get("bin_name") or binstall.bin_name
    if not bin_name:
        msg = (
            f"Missing required key 'bin_name' in [common] section of {config_file}; "
            "set common.bin_name or common.binstall.bin_name"
        )
        raise StageError(msg)
    artefacts = _make_artefacts(
        common,
        target_cfg,
        config_file,
        allow_empty=binstall.enabled,
    )

    return StagingConfig(
        workspace=workspace,
        bin_name=bin_name,
        dist_dir=common.get("dist_dir", "dist"),
        checksum_algorithm=algorithm,
        artefacts=artefacts,
        platform=target_cfg["platform"],
        arch=target_cfg["arch"],
        target=target_cfg["target"],
        bin_ext=target_cfg.get("bin_ext", ""),
        staging_dir_template=target_cfg.get(
            "staging_dir_template",
            common.get("staging_dir_template", "{bin_name}_{platform}_{arch}"),
        ),
        target_key=target_key,
        binstall=binstall,
    )


def _load_toml(path: Path) -> dict[str, typ.Any]:
    """Load and parse a TOML file."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _extract_sections(
    data: dict[str, typ.Any], config_path: Path, target_key: str
) -> tuple[dict[str, typ.Any], dict[str, typ.Any]]:
    """Extract common and target-specific sections from config data."""
    try:
        common = data["common"]
        target_cfg = data["targets"][target_key]
    except KeyError as exc:
        msg = f"Missing configuration key in {config_path}: {exc}"
        raise StageError(msg) from exc
    return common, target_cfg


def _validate_checksum(name: str | None) -> str:
    """Validate and normalize checksum algorithm name."""
    algorithm = (name or "sha256").lower()
    supported = {item.lower() for item in hashlib.algorithms_available}
    if algorithm not in supported:
        msg = f"Unsupported checksum algorithm: {algorithm}"
        raise StageError(msg)
    return algorithm


def _require_string(entry: dict[str, typ.Any], key: str, prefix: str) -> str:
    """Validate and return a required non-empty string field."""
    value = entry.get(key)
    if not isinstance(value, str):
        msg = f"Artefact '{key}' must be a string, got {type(value).__name__} {prefix}"
        raise StageError(msg)
    if not value:
        msg = f"Missing required artefact key '{key}' {prefix}"
        raise StageError(msg)
    return value


def _optional_field(  # noqa: UP047
    value: _T | None,
    key: str,
    prefix: str,
    expected_type: type[_T],
) -> _T | None:
    """Validate and return an optional typed field."""
    if value is None:
        return None
    if not isinstance(value, expected_type):
        msg = (
            f"Artefact '{key}' must be a {expected_type.__name__},"
            f" got {type(value).__name__} {prefix}"
        )
        raise StageError(msg)
    return value


def _optional_bool(
    entry: dict[str, typ.Any], key: str, prefix: str, *, default: bool
) -> bool:
    """Validate and return an optional boolean field with default."""
    result = _optional_field(entry.get(key, default), key, prefix, bool)
    assert result is not None  # noqa: S101  # default is always bool, never None
    return result


def _optional_string_list(
    entry: dict[str, typ.Any], key: str, prefix: str
) -> list[str]:
    """Validate and return an optional list of strings."""
    value = entry.get(key, [])
    if not isinstance(value, list):
        msg = f"Artefact '{key}' must be a list, got {type(value).__name__} {prefix}"
        raise StageError(msg)
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            msg = (
                f"Artefact {key}[{idx}] must be a string, "
                f"got {type(item).__name__} {prefix}"
            )
            raise StageError(msg)
    return value


def _optional_string(
    entry: dict[str, typ.Any], key: str, prefix: str, default: str | None
) -> str | None:
    """Validate and return an optional string field."""
    return _optional_field(entry.get(key, default), key, prefix, str)


def _parse_artefact_entry(
    entry: dict[str, typ.Any], index: int, config_path: Path
) -> ArtefactConfig:
    """Parse and validate a single artefact entry."""
    prefix = f"in entry #{index} of {config_path}"
    for key in ("destination", "dest"):
        value = entry.get(key)
        if key in entry and not isinstance(value, str):
            msg = (
                f"Artefact '{key}' must be a string, "
                f"got {type(value).__name__} {prefix}"
            )
            raise StageError(msg)
    if "destination" in entry and "dest" in entry:
        msg = f"Artefact must not define both 'destination' and 'dest' {prefix}"
        raise StageError(msg)
    destination = entry.get("destination", entry.get("dest"))
    return ArtefactConfig(
        source=_require_string(entry, "source", prefix),
        required=_optional_bool(entry, "required", prefix, default=True),
        output=entry.get("output"),
        destination=destination,
        alternatives=_optional_string_list(entry, "alternatives", prefix),
    )


def _make_artefacts(
    common: dict[str, typ.Any],
    target_cfg: dict[str, typ.Any],
    config_path: Path,
    *,
    allow_empty: bool = False,
) -> list[ArtefactConfig]:
    """Build list of ArtefactConfig from common and target sections."""
    entries = [*common.get("artefacts", []), *target_cfg.get("artefacts", [])]
    if not entries:
        if allow_empty:
            return []
        msg = "No artefacts configured to stage."
        raise StageError(msg)
    return [
        _parse_artefact_entry(entry, index, config_path)
        for index, entry in enumerate(entries, start=1)
    ]


def _optional_mapping(
    section: dict[str, typ.Any], key: str, label: str, config_path: Path
) -> dict[str, typ.Any]:
    """Return an optional nested mapping from a config section."""
    value = section.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"Configuration key [{label}.{key}] in {config_path} must be a table"
        raise StageError(msg)
    return typ.cast("dict[str, typ.Any]", value)


def _merge_binstall_entries(
    common_entry: dict[str, typ.Any], target_entry: dict[str, typ.Any]
) -> dict[str, typ.Any]:
    """Merge common and target-specific binstall configuration."""
    return common_entry | target_entry


def _make_binstall_config(
    common: dict[str, typ.Any], target_cfg: dict[str, typ.Any], config_path: Path
) -> BinstallConfig:
    """Build BinstallConfig from common and target-specific sections."""
    common_entry = _optional_mapping(common, "binstall", "common", config_path)
    target_entry = _optional_mapping(
        target_cfg,
        "binstall",
        "targets.<target>",
        config_path,
    )
    entry = _merge_binstall_entries(common_entry, target_entry)
    prefix = f"in [binstall] configuration of {config_path}"
    return BinstallConfig(
        enabled=_optional_bool(entry, "enabled", prefix, default=False),
        manifest_path=_optional_string(entry, "manifest_path", prefix, "Cargo.toml")
        or "Cargo.toml",
        package_name=_optional_string(entry, "package_name", prefix, None),
        version=_optional_string(entry, "version", prefix, None),
        bin_name=_optional_string(entry, "bin_name", prefix, None),
        archive_name=_optional_string(
            entry, "archive_name", prefix, "{package_name}-{version}-{target}.tar.gz"
        )
        or "{package_name}-{version}-{target}.tar.gz",
        binary_source=_optional_string(
            entry,
            "binary_source",
            prefix,
            "target/{target}/release/{bin_name}{bin_ext}",
        )
        or "target/{target}/release/{bin_name}{bin_ext}",
        binary_name=_optional_string(
            entry, "binary_name", prefix, "{bin_name}{bin_ext}"
        )
        or "{bin_name}{bin_ext}",
        output=_optional_string(entry, "output", prefix, "binstall_archive_path")
        or "binstall_archive_path",
    )


def _require_keys(
    section: dict[str, typ.Any], keys: set[str], label: str, config_path: Path
) -> None:
    """Ensure ``section`` defines all required ``keys``."""
    missing = sorted(key for key in keys if key not in section)
    if missing:
        joined = ", ".join(missing)
        msg = f"Missing required key(s) {joined} in [{label}] section of {config_path}"
        raise StageError(msg)
