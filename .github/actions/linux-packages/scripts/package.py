#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "cyclopts>=2.9.0",
#   "plumbum>=1.8",
#   "pyyaml>=6.0",
#   "typer>=0.12",
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
"""

from __future__ import annotations

import gzip
import os
import re
import sys
import types
import typing as typ
from pathlib import Path

import cyclopts
import yaml
from cyclopts import App, Parameter
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from .script_utils import (
        ensure_directory,
        ensure_exists,
        get_command,
        run_cmd,
    )
else:  # pragma: no cover - runtime fallback when executed as a script
    try:
        from .script_utils import (
            ensure_directory,
            ensure_exists,
            get_command,
            run_cmd,
        )
    except ImportError:  # pragma: no cover - fallback for direct execution
        import importlib.util

        _PKG_DIR = Path(__file__).resolve().parent
        _PKG_NAME = "linux_packages_scripts"
        pkg_module = sys.modules.get(_PKG_NAME)
        if pkg_module is None:
            pkg_module = types.ModuleType(_PKG_NAME)
            pkg_module.__path__ = [str(_PKG_DIR)]  # type: ignore[attr-defined]
            sys.modules[_PKG_NAME] = pkg_module
        if not hasattr(pkg_module, "load_sibling"):
            spec = importlib.util.spec_from_file_location(
                _PKG_NAME, _PKG_DIR / "__init__.py"
            )
            if spec is None or spec.loader is None:
                raise ImportError(name="script_utils") from None
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PKG_NAME] = module
            spec.loader.exec_module(module)
            pkg_module = module

        load_sibling = typ.cast(
            "typ.Callable[[str], types.ModuleType]", pkg_module.load_sibling
        )
        helpers = typ.cast("typ.Any", load_sibling("script_utils"))
        ensure_directory = helpers.ensure_directory
        ensure_exists = helpers.ensure_exists
        get_command = helpers.get_command
        run_cmd = helpers.run_cmd


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

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ())
if existing_config is None:
    app.config = (_env_config,)
else:
    app.config = (*tuple(existing_config), _env_config)


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


def map_target_to_arch(target: str) -> str:
    """Map a Rust target triple to nFPM/GOARCH arch strings."""
    t = target.lower()
    if t.startswith(("x86_64-", "x86_64_")):
        return "amd64"
    if t.startswith(("aarch64-", "arm64-")):
        return "arm64"
    if t.startswith(("i686-", "i586-", "i386-")):
        return "386"
    if t.startswith(("armv7-", "armv6-", "arm-")):
        return "arm"
    if t.startswith("riscv64-"):
        return "riscv64"
    if t.startswith(("powerpc64le-", "ppc64le-")):
        return "ppc64le"
    if t.startswith("s390x-"):
        return "s390x"
    if t.startswith(("loongarch64-", "loong64-")):
        return "loong64"
    raise PackagingError.unsupported_target(target)


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
        file_info = entry.get("file_info")
        if file_info:
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
        dest_dir = f"/usr/share/man/man{section}/{dest_filename}"
        gz = ensure_gz(src, stage)
        entries.append(
            {
                "src": gz.as_posix(),
                "dst": dest_dir,
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
            lowered = token.lower()
            if lowered not in entries:
                entries.append(lowered)
    return entries


def _coerce_optional_path(value: Path | None, env_var: str) -> Path | None:
    """Return ``None`` when the env var supplies an empty path."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _coerce_path_list(values: list[Path] | None, env_var: str) -> list[Path]:
    """Return cleaned path list honouring blank env defaults."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return []
    cleaned: list[Path] = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        cleaned.append(Path(text))
    return cleaned


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
    bin_value = bin_name.strip()
    if not bin_value:
        raise PackagingError.missing_bin()
    package_value = (package_name or bin_value).strip() or bin_value
    version_value = version.strip().lstrip("v")
    if not version_value:
        raise PackagingError.missing_version()

    target_value = target.strip() or "x86_64-unknown-linux-gnu"
    release_value = (release or "1").strip() or "1"
    arch_value = (arch or map_target_to_arch(target_value)).strip()

    binary_root = _coerce_optional_path(
        binary_dir,
        "INPUT_BINARY_DIR",
    ) or Path("target")
    outdir_path = _coerce_optional_path(
        outdir,
        "INPUT_OUTDIR",
    ) or Path("dist")
    config_out_path = _coerce_optional_path(
        config_out,
        "INPUT_CONFIG_PATH",
    ) or Path("dist/nfpm.yaml")
    man_section_value = (man_section or "1").strip() or "1"
    man_stage_path = _coerce_optional_path(
        man_stage,
        "INPUT_MAN_STAGE",
    ) or Path("dist/.man")

    bin_path = binary_root / target_value / "release" / bin_value
    ensure_exists(bin_path, "built binary not found; build first")
    ensure_directory(outdir_path)
    ensure_directory(config_out_path.parent)

    license_file = Path("LICENSE")
    contents: list[dict[str, typ.Any]] = [
        {
            "src": bin_path.as_posix(),
            "dst": f"/usr/bin/{bin_value}",
            "file_info": {"mode": "0755"},
        }
    ]
    if license_file.exists():
        contents.append(
            {
                "src": license_file.as_posix(),
                "dst": f"/usr/share/doc/{package_value}/copyright",
                "file_info": {"mode": "0644"},
            }
        )

    man_sources = _coerce_path_list(man_paths, "INPUT_MAN_PATHS")
    man_entries = build_man_entries(man_sources, man_section_value, man_stage_path)
    contents.extend(man_entries)

    deb_requires = _normalise_list(deb_depends, default=[])
    rpm_requires = (
        _normalise_list(rpm_depends, default=[])
        if rpm_depends is not None
        else list(deb_requires)
    )

    config: dict[str, typ.Any] = {
        "name": package_value,
        "arch": arch_value,
        "platform": "linux",
        "version": version_value,
        "release": release_value,
        "section": (section or "").strip(),
        "priority": "optional",
        "maintainer": (maintainer or "").strip(),
        "homepage": (homepage or "").strip(),
        "license": (license_ or "").strip(),
        "description": (description or "").strip(),
        "contents": normalise_file_modes(contents),
        "overrides": {
            "deb": {"depends": deb_requires},
            "rpm": {"depends": rpm_requires},
        },
    }

    config_out_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {config_out_path}")

    nfpm = get_command("nfpm")

    resolved_formats = _normalise_list(formats, default=["deb"])
    if not resolved_formats:
        raise PackagingError.missing_formats()

    rc_any = 0
    single_format = len(resolved_formats) == 1
    for fmt in resolved_formats:
        print(f"→ nfpm package -p {fmt} -f {config_out_path} -t {outdir_path}/")
        try:
            run_cmd(
                nfpm[
                    "package",
                    "-p",
                    fmt,
                    "-f",
                    str(config_out_path),
                    "-t",
                    str(outdir_path),
                ]
            )
        except ProcessExecutionError as pe:
            print(
                f"error: nfpm failed for format '{fmt}' (exit {pe.retcode})",
                file=sys.stderr,
            )
            rc_any = rc_any or pe.retcode
            if single_format:
                raise SystemExit(pe.retcode) from pe
        else:
            print(f"✓ built {fmt} packages in {outdir_path}")

    if rc_any:
        raise SystemExit(rc_any)


def run() -> None:
    """Execute the CLI, mapping packaging errors to user-friendly exits."""
    try:
        app()
    except PackagingError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    run()
