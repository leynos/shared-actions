#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9.0,<3.0",
#   "plumbum>=1.8,<2.0",
#   "pyyaml>=6.0,<7.0",
#   "typer>=0.9,<1.0",
# ]
# ///
"""
Minimal nFPM packager for Rust binaries, with manpage support.

Examples
--------
  uv run package.py --bin-name rust-toy-app \
    --target x86_64-unknown-linux-gnu --version 1.2.3 \
    --formats deb rpm \
    --man-paths doc/rust-toy-app.1 doc/rust-toy-app-subcmd.1

Assumes the binary already exists at:
  target/<target>/release/<bin-name>
relative to the working directory (the action runs from ``project-dir``).
"""

from __future__ import annotations

import dataclasses
import gzip
import os
import re
import shlex
import sys
import typing as typ
from pathlib import Path

import cyclopts
import yaml
from cyclopts import App, Parameter
from plumbum.commands.processes import ProcessExecutionError

try:  # pragma: no cover - exercised when packaged as a module
    from . import _bootstrap  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - fallback for direct execution
    SCRIPTS_DIR = Path(__file__).resolve().parent
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import _bootstrap  # type: ignore[import-not-found]

if typ.TYPE_CHECKING:
    from .script_utils import ensure_directory, ensure_exists, get_command, run_cmd
    from .targets import TargetInfo, TargetResolutionError


try:
    from . import targets as _targets_module
except ImportError:  # pragma: no cover - executed for direct runs
    _targets_module = typ.cast("typ.Any", _bootstrap.load_helper_module("targets"))


TargetInfo = _targets_module.TargetInfo  # type: ignore[assignment]
TargetResolutionError = _targets_module.TargetResolutionError  # type: ignore[assignment]
resolve_target = _targets_module.resolve_target  # type: ignore[assignment]


def _import_script_utils() -> tuple[typ.Any, typ.Any, typ.Any, typ.Any]:
    try:
        from .script_utils import ensure_directory, ensure_exists, get_command, run_cmd
    except ImportError:  # pragma: no cover - executed for direct runs
        helpers = typ.cast("typ.Any", _bootstrap.load_helper_module("script_utils"))
        return (
            helpers.ensure_directory,
            helpers.ensure_exists,
            helpers.get_command,
            helpers.run_cmd,
        )
    else:
        return ensure_directory, ensure_exists, get_command, run_cmd


ensure_directory, ensure_exists, get_command, run_cmd = _import_script_utils()


class PackagingError(RuntimeError):
    """Raised when packaging inputs cannot be processed."""

    @classmethod
    def unsupported_target(cls, target: str) -> PackagingError:
        """Return an error describing an unsupported target triple."""
        return cls(f"unsupported target triple: {target}")

    @classmethod
    def invalid_mode(cls, mode: str, target_desc: str) -> PackagingError:
        """Return an error describing an invalid file mode entry."""
        return cls(f"invalid file mode '{mode}' for {target_desc}")

    @classmethod
    def missing_bin(cls) -> PackagingError:
        """Return an error indicating the binary name input was omitted."""
        return cls("bin-name input is required")

    @classmethod
    def missing_version(cls) -> PackagingError:
        """Return an error indicating the version input was omitted."""
        return cls("version input is required")

    @classmethod
    def missing_formats(cls) -> PackagingError:
        """Return an error describing a missing packaging format list."""
        return cls("no packaging formats provided")


SECTION_RE = re.compile(r"\.(\d[\w-]*)($|\.gz$)")

_APP_CONFIG = {"config": (cyclopts.config.Env("INPUT_", command=False),)}
app = App(**typ.cast("dict[str, typ.Any]", _APP_CONFIG))


def _fail(message: str, *, code: int = 2) -> typ.NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


class OctalInt(int):
    """Integer subclass that renders as a zero-padded octal literal."""

    def __new__(cls, value: int, *, width: int = 4) -> OctalInt:
        """Initialise the integer and remember the desired octal width."""
        obj = super().__new__(cls, value)
        obj._octal_width = width
        return obj


def _represent_octal_int(dumper: yaml.Dumper, data: OctalInt) -> yaml.ScalarNode:
    width = getattr(data, "_octal_width", 4)
    return dumper.represent_scalar(
        "tag:yaml.org,2002:int", format(int(data), f"0{width}o")
    )


yaml.SafeDumper.add_representer(OctalInt, _represent_octal_int)


@dataclasses.dataclass(slots=True)
class ResolvedInputs:
    """Normalised values derived from CLI and environment inputs."""

    package: str
    bin_name: str
    version: str
    target: str
    release: str
    arch: str
    man_section: str


@dataclasses.dataclass(slots=True)
class ResolvedPaths:
    """Concrete filesystem paths used during packaging."""

    binary_root: Path
    outdir: Path
    config_out: Path
    man_stage: Path
    bin_path: Path


def _resolve_target_info(target: str) -> TargetInfo:
    """Return metadata for *target* or raise a PackagingError."""
    try:
        return resolve_target(target)
    except TargetResolutionError as exc:  # pragma: no cover - error path
        raise PackagingError.unsupported_target(target) from exc


def _normalise_inputs(
    package_name: str | None,
    bin_name: str,
    target: str,
    version: str,
    release: str | None,
    arch: str | None,
    man_section: str | None,
) -> tuple[ResolvedInputs, TargetInfo]:
    """Validate and coerce raw inputs into concrete values."""
    bin_value = bin_name.strip()
    if not bin_value:
        raise PackagingError.missing_bin()

    package_value = (package_name or bin_value).strip() or bin_value
    version_value = version.strip().lstrip("v")
    if not version_value:
        raise PackagingError.missing_version()

    target_value = target.strip() or "x86_64-unknown-linux-gnu"
    target_info = _resolve_target_info(target_value)
    arch_value = (arch or target_info.nfpm_arch).strip() or target_info.nfpm_arch
    release_value = (release or "1").strip() or "1"
    man_section_value = (man_section or "1").strip() or "1"

    return (
        ResolvedInputs(
            package=package_value,
            bin_name=bin_value,
            version=version_value,
            target=target_value,
            release=release_value,
            arch=arch_value,
            man_section=man_section_value,
        ),
        target_info,
    )


def _resolve_paths(
    inputs: ResolvedInputs,
    binary_dir: Path | None,
    outdir: Path | None,
    config_out: Path | None,
    man_stage: Path | None,
) -> ResolvedPaths:
    """Resolve filesystem paths, honouring blank environment overrides."""
    binary_root = _coerce_path(binary_dir, "INPUT_BINARY_DIR", default=Path("target"))
    outdir_path = _coerce_path(outdir, "INPUT_OUTDIR", default=Path("dist"))
    config_out_path = _coerce_path(
        config_out,
        "INPUT_CONFIG_PATH",
        default=Path("dist/nfpm.yaml"),
    )
    man_stage_path = _coerce_path(
        man_stage,
        "INPUT_MAN_STAGE",
        default=Path("dist/.man"),
    )

    bin_path = binary_root / inputs.target / "release" / inputs.bin_name
    ensure_exists(bin_path, "built binary not found; build first")
    ensure_directory(outdir_path)
    ensure_directory(config_out_path.parent)

    return ResolvedPaths(
        binary_root=binary_root,
        outdir=outdir_path,
        config_out=config_out_path,
        man_stage=man_stage_path,
        bin_path=bin_path,
    )


def _resolve_dependencies(
    deb_depends: list[str] | None,
    rpm_depends: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Return Debian and RPM dependency lists honouring fallbacks."""
    deb_requires = _normalise_list(deb_depends, default=[])
    rpm_candidates = _normalise_list(rpm_depends, default=[])
    rpm_requires = rpm_candidates if rpm_candidates else list(deb_requires)
    return deb_requires, rpm_requires


def _build_contents(
    inputs: ResolvedInputs,
    paths: ResolvedPaths,
    man_sources: list[Path],
) -> list[dict[str, typ.Any]]:
    """Assemble nfpm contents entries for the binary, license, and man pages."""
    contents: list[dict[str, typ.Any]] = [
        {
            "src": paths.bin_path.as_posix(),
            "dst": f"/usr/bin/{inputs.bin_name}",
            "file_info": {"mode": "0755"},
        }
    ]

    license_file = Path("LICENSE")
    if license_file.exists():
        contents.append(
            {
                "src": license_file.as_posix(),
                "dst": f"/usr/share/doc/{inputs.package}/copyright",
                "file_info": {"mode": "0644"},
            }
        )

    man_entries = build_man_entries(man_sources, inputs.man_section, paths.man_stage)
    contents.extend(man_entries)
    return normalise_file_modes(contents)


def _clean(value: str | None) -> str:
    """Return a whitespace-trimmed string for optional metadata."""
    return (value or "").strip()


def _write_config(
    inputs: ResolvedInputs,
    target_info: TargetInfo,
    paths: ResolvedPaths,
    contents: list[dict[str, typ.Any]],
    maintainer: str | None,
    homepage: str | None,
    license_: str | None,
    section: str | None,
    description: str | None,
    deb_requires: list[str],
    rpm_requires: list[str],
) -> Path:
    """Write the nfpm configuration file and return its path."""
    config: dict[str, typ.Any] = {
        "name": inputs.package,
        "arch": inputs.arch,
        "platform": target_info.platform,
        "version": inputs.version,
        "release": inputs.release,
        "section": _clean(section),
        "priority": "optional",
        "maintainer": _clean(maintainer),
        "homepage": _clean(homepage),
        "license": _clean(license_),
        "description": _clean(description),
        "contents": contents,
        "overrides": {
            "deb": {"depends": deb_requires},
            "rpm": {"depends": rpm_requires},
        },
    }

    paths.config_out.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {paths.config_out}")
    return paths.config_out


def _package_formats(
    formats: list[str],
    config_path: Path,
    outdir: Path,
) -> None:
    """Invoke nfpm for each requested format and surface aggregated failures."""
    nfpm = get_command("nfpm")
    failures: list[tuple[str, int]] = []
    single_format = len(formats) == 1

    for fmt in formats:
        cmd = nfpm[
            "package",
            "--packager",
            fmt,
            "-f",
            str(config_path),
            "-t",
            str(outdir),
        ]
        print(f"→ {shlex.join(cmd.formulate())}")
        try:
            run_cmd(cmd)
        except ProcessExecutionError as pe:
            retcode = int(pe.retcode or 1)
            print(
                f"error: nfpm failed for format '{fmt}' (exit {retcode})",
                file=sys.stderr,
            )
            if single_format:
                raise SystemExit(retcode) from pe
            failures.append((fmt, retcode))
        else:
            print(f"✓ built {fmt} packages in {outdir}")

    if failures:
        for fmt, retcode in failures:
            print(
                f"format '{fmt}' failed with exit code {retcode}",
                file=sys.stderr,
            )
        raise SystemExit(failures[0][1])


def infer_section(path: Path, default: str) -> str:
    """Extract the manpage section from ``path`` or fall back to ``default``."""
    m = SECTION_RE.search(path.name)
    return m.group(1) if m else default


def stem_without_section(filename: str) -> str:
    """Drop trailing .<section>(.gz)? once; keep the rest intact."""
    name = filename.removesuffix(".gz")
    return SECTION_RE.sub("", name)


def ensure_gz(src: Path, dst_dir: Path) -> Path:
    """Return a gzipped file path in ``dst_dir`` for ``src``."""
    if src.suffix == ".gz":
        return src
    ensure_directory(dst_dir)
    gz_path = dst_dir / f"{src.name}.gz"
    with (
        src.open("rb") as fin,
        gz_path.open("wb") as fout,
        gzip.GzipFile(
            filename="",
            fileobj=fout,
            mode="wb",
            mtime=0,
            compresslevel=9,
        ) as gz,
    ):
        gz.write(fin.read())
    return gz_path


def normalise_file_modes(entries: list[dict[str, typ.Any]]) -> list[dict[str, typ.Any]]:
    """Convert string ``mode`` values to octal-preserving integers."""
    normalised: list[dict[str, typ.Any]] = []
    for entry in entries:
        new_entry = dict(entry)
        if file_info := entry.get("file_info"):
            new_info = dict(file_info)
            mode = new_info.get("mode")
            if isinstance(mode, str):
                cleaned = mode.strip()
                try:
                    value = int(cleaned, 8)
                except ValueError as exc:
                    target_desc = new_entry.get("dst") or new_entry.get("src", "entry")
                    raise PackagingError.invalid_mode(mode, target_desc) from exc
                new_info["mode"] = OctalInt(value, width=len(cleaned))
            new_entry["file_info"] = new_info
        normalised.append(new_entry)
    return normalised


def build_man_entries(
    man_sources: list[Path],
    default_section: str,
    stage_dir: Path,
) -> list[dict[str, typ.Any]]:
    """Return nFPM ``contents`` entries for the provided man pages."""
    if not man_sources:
        return []

    entries: list[dict[str, typ.Any]] = []
    stage = ensure_directory(stage_dir)
    for src in man_sources:
        ensure_exists(src, "manpage not found")
        section = infer_section(src, default_section)
        base_no_section = stem_without_section(src.name)
        dest_filename = f"{base_no_section}.{section}.gz"
        dest_path = f"/usr/share/man/man{section}/{dest_filename}"
        gz = ensure_gz(src, stage)
        entries.append(
            {
                "src": gz.as_posix(),
                "dst": dest_path,
                "file_info": {"mode": "0644"},
            }
        )
    return entries


def _normalise_list(values: list[str] | None, *, default: list[str]) -> list[str]:
    entries: list[str] = []
    source = values if values is not None else default
    for item in source:
        for token in re.split(r"[\s,]+", item.strip()):
            if not token:
                continue
            if token not in entries:
                entries.append(token)
    return entries


def _coerce_optional_path(
    value: Path | None,
    env_var: str,
    *,
    default: Path | None = None,
) -> Path | None:
    """Return the path value unless the environment overrides with blank text."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return default
    if value is None:
        return default
    text = str(value).strip()
    return default if not text else Path(text)


def _coerce_path_list(values: list[Path] | None, env_var: str) -> list[Path]:
    """Return cleaned path list honouring blank env defaults."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return []
    return [Path(text) for value in values or [] if (text := str(value).strip())]


def _coerce_path(value: Path | None, env_var: str, *, default: Path) -> Path:
    """Return a concrete path, falling back to *default* when unset."""
    result = _coerce_optional_path(value, env_var, default=default)
    return result if result is not None else default


@app.default
def main(
    *,
    package_name: str | None = None,
    bin_name: typ.Annotated[str, Parameter(required=True)],
    target: str = "x86_64-unknown-linux-gnu",
    version: typ.Annotated[str, Parameter(required=True)],
    formats: list[str] | None = None,
    release: str | None = None,
    arch: str | None = None,
    maintainer: str | None = None,
    homepage: str | None = None,
    license_: typ.Annotated[str | None, Parameter(env_var="INPUT_LICENSE")] = None,
    section: str | None = None,
    description: str | None = None,
    man_paths: typ.Annotated[
        list[Path] | None, Parameter(env_var="INPUT_MAN_PATHS")
    ] = None,
    man_section: str | None = None,
    man_stage: Path | None = None,
    outdir: Path | None = None,
    binary_dir: Path | None = None,
    config_out: typ.Annotated[
        Path | None, Parameter(env_var="INPUT_CONFIG_PATH")
    ] = None,
    deb_depends: list[str] | None = None,
    rpm_depends: list[str] | None = None,
) -> None:
    """Build packages for a Rust binary using nFPM configuration derived from inputs."""
    inputs, target_info = _normalise_inputs(
        package_name,
        bin_name,
        target,
        version,
        release,
        arch,
        man_section,
    )
    paths = _resolve_paths(inputs, binary_dir, outdir, config_out, man_stage)

    man_sources = _coerce_path_list(man_paths, "INPUT_MAN_PATHS")
    contents = _build_contents(inputs, paths, man_sources)
    deb_requires, rpm_requires = _resolve_dependencies(deb_depends, rpm_depends)

    config_path = _write_config(
        inputs,
        target_info,
        paths,
        contents,
        maintainer,
        homepage,
        license_,
        section,
        description,
        deb_requires,
        rpm_requires,
    )

    resolved_formats = _normalise_list(formats, default=["deb"])
    if not resolved_formats:
        raise PackagingError.missing_formats()
    _package_formats(resolved_formats, config_path, paths.outdir)


def run() -> None:
    """Execute the CLI, mapping packaging errors to user-friendly exits."""
    try:
        app()
    except PackagingError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    run()
