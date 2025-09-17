#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "typer>=0.12",
#   "plumbum>=1.8",
#   "pyyaml>=6.0",
# ]
# ///
"""
Minimal nFPM packager for Rust binaries, with manpage support.

Examples
--------
  uv run package.py --name rust-toy-app --bin-name rust-toy-app \
    --target x86_64-unknown-linux-gnu --version 1.2.3 \
    --formats deb,rpm \
    --man doc/rust-toy-app.1 --man doc/rust-toy-app-subcmd.1

Assumes the binary already exists at:
  target/<target>/release/<bin-name>
"""

from __future__ import annotations

import gzip
import re
import types
import typing as typ
from pathlib import Path

import typer
import yaml
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from .script_utils import (
        ensure_directory,
        ensure_exists,
        get_command,
        run_cmd,
    )
else:
    try:
        from .script_utils import (
            ensure_directory,
            ensure_exists,
            get_command,
            run_cmd,
        )
    except ImportError:  # pragma: no cover - fallback for direct execution
        import importlib.util
        import sys

        _PKG_DIR = Path(__file__).resolve().parent
        _PKG_NAME = "rust_build_release_scripts"
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

app = typer.Typer(add_completion=False, no_args_is_help=True)

SECTION_RE = re.compile(
    r"\.(\d[\w-]*)($|\.gz$)"
)  # captures e.g. ".1", ".1p", ".8r", with/without .gz


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
        return "arm"  # GOARM nuance out of scope here
    if t.startswith("riscv64-"):
        return "riscv64"
    if t.startswith(("powerpc64le-", "ppc64le-")):
        return "ppc64le"
    if t.startswith("s390x-"):
        return "s390x"
    if t.startswith(("loongarch64-", "loong64-")):
        return "loong64"
    typer.secho(
        f"error: unsupported target triple: {target}", fg=typer.colors.RED, err=True
    )
    raise typer.Exit(2)


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
                    typer.secho(
                        f"error: invalid file mode '{mode}' for {target_desc}",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    raise typer.Exit(2) from exc
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


NAME_OPTION = typer.Option(..., "--name", help="Package name.")
BIN_NAME_OPTION = typer.Option(..., "--bin-name", help="Installed binary name.")
TARGET_OPTION = typer.Option(
    "x86_64-unknown-linux-gnu",
    "--target",
    help="Rust target triple used for the build.",
)
VERSION_OPTION = typer.Option(
    ..., "--version", help="Version (Debian-friendly: starts with a digit)."
)
RELEASE_OPTION = typer.Option("1", "--release", help="Package release/revision.")
ARCH_OPTION = typer.Option(
    None, "--arch", help="Override nFPM/GOARCH arch (e.g. amd64, arm64)."
)
FORMATS_OPTION = typer.Option(
    "deb,rpm",
    "--formats",
    help="Comma-separated list: deb,rpm,apk,archlinux,ipk,srpm",
)
OUTDIR_OPTION = typer.Option(Path("dist"), "--outdir", help="Where to place packages.")
MAINTAINER_OPTION = typer.Option("Your Name <you@example.com>", "--maintainer")
HOMEPAGE_OPTION = typer.Option("https://example.com", "--homepage")
LICENSE_OPTION = typer.Option("MIT", "--license")
SECTION_OPTION = typer.Option("utils", "--section")
DESCRIPTION_OPTION = typer.Option("A fast toy app written in Rust.", "--description")
DEB_DEPENDS_OPTION = typer.Option(
    None, "--deb-depends", help="Repeatable. Debian runtime deps."
)
RPM_DEPENDS_OPTION = typer.Option(
    None, "--rpm-depends", help="Repeatable. RPM runtime deps."
)
BINARY_DIR_OPTION = typer.Option(
    Path("target"), "--binary-dir", help="Root of Cargo target dir."
)
CONFIG_OUT_OPTION = typer.Option(
    Path("dist/nfpm.yaml"),
    "--config-out",
    help="Path to write generated nfpm.yaml.",
)
MAN_OPTION = typer.Option(
    None,
    "--man",
    help="Repeatable. Paths to manpages (e.g. doc/app.1 or app.1.gz).",
)
MAN_SECTION_OPTION = typer.Option(
    "1", "--man-section", help="Default man section if the filename lacks one."
)
MAN_STAGE_OPTION = typer.Option(
    Path("dist/.man"), "--man-stage", help="Where to stage gzipped manpages."
)


@app.command()
def main(
    name: str = NAME_OPTION,
    bin_name: str = BIN_NAME_OPTION,
    target: str = TARGET_OPTION,
    version: str = VERSION_OPTION,
    release: str = RELEASE_OPTION,
    arch: str | None = ARCH_OPTION,
    formats: str = FORMATS_OPTION,
    outdir: Path = OUTDIR_OPTION,
    maintainer: str = MAINTAINER_OPTION,
    homepage: str = HOMEPAGE_OPTION,
    license_: str = LICENSE_OPTION,
    section: str = SECTION_OPTION,
    description: str = DESCRIPTION_OPTION,
    deb_depends: list[str] | None = DEB_DEPENDS_OPTION,
    rpm_depends: list[str] | None = RPM_DEPENDS_OPTION,
    binary_dir: Path = BINARY_DIR_OPTION,
    config_out: Path = CONFIG_OUT_OPTION,
    man: list[Path] | None = MAN_OPTION,
    man_section: str = MAN_SECTION_OPTION,
    man_stage: Path = MAN_STAGE_OPTION,
) -> None:
    """Build packages for a Rust binary using nFPM configuration derived from inputs."""
    # Normalise/derive fields.
    ver = version.lstrip("v")  # Debian wants a digit first.
    arch_val = arch or map_target_to_arch(target)
    bin_path = binary_dir / target / "release" / bin_name
    ensure_exists(bin_path, "built binary not found; build first")
    ensure_directory(outdir)
    ensure_directory(config_out.parent)

    license_file = Path("LICENSE")
    contents: list[dict[str, typ.Any]] = [
        {
            "src": bin_path.as_posix(),
            "dst": f"/usr/bin/{bin_name}",
            "file_info": {"mode": "0755"},
        }
    ]
    if license_file.exists():
        contents.append(
            {
                "src": license_file.as_posix(),
                "dst": f"/usr/share/doc/{name}/copyright",
                "file_info": {"mode": "0644"},
            }
        )

    man_entries = build_man_entries(man or [], man_section, man_stage)
    contents.extend(man_entries)

    deb_requires = list(deb_depends or [])
    rpm_requires = list(rpm_depends) if rpm_depends else list(deb_requires)

    config: dict[str, typ.Any] = {
        "name": name,
        "arch": arch_val,
        "platform": "linux",
        "version": ver,
        "release": release,
        "section": section,
        "priority": "optional",
        "maintainer": maintainer,
        "homepage": homepage,
        "license": license_,
        "description": description,
        "contents": normalise_file_modes(contents),
        "overrides": {
            "deb": {"depends": deb_requires},
            "rpm": {"depends": rpm_requires},
        },
    }

    config_out.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    typer.secho(f"Wrote {config_out}", fg=typer.colors.GREEN)

    # Ensure nfpm is available.
    nfpm = get_command("nfpm")

    # Run nfpm for each requested format.
    formats_list = [s.strip() for s in formats.split(",") if s.strip()]
    if not formats_list:
        typer.secho(
            "error: no packaging formats provided", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(2)

    rc_any = 0
    single_format = len(formats_list) == 1
    for fmt in formats_list:
        typer.echo(f"→ nfpm package -p {fmt} -f {config_out} -t {outdir}/")
        try:
            run_cmd(
                nfpm[
                    "package",
                    "-p",
                    fmt,
                    "-f",
                    str(config_out),
                    "-t",
                    str(outdir),
                ]
            )
        except ProcessExecutionError as pe:
            typer.secho(
                f"nfpm failed for format '{fmt}' (exit {pe.retcode})",
                fg=typer.colors.RED,
                err=True,
            )
            if single_format:
                raise typer.Exit(pe.retcode) from pe
            rc_any = rc_any or pe.retcode
        else:
            typer.secho(f"✓ built {fmt} packages in {outdir}", fg=typer.colors.GREEN)

    raise typer.Exit(rc_any)


if __name__ == "__main__":
    app()
